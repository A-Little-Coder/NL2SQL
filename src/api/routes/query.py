"""核心查询接口（决策 49 重构 + 决策 50 真流式）

POST /api/v1/query — SSE 流式响应

请求体含 db_id 字段，通过 DbContextPool 按需获取对应数据库的主图。

决策 50：
- 真流式：每个节点完成时立即推送 SSE 事件，不再"先攒后吐"
- 思考链：LLM 调用过程中 reasoning_content 通过 llm_thinking 事件实时推送
- 心跳：15 秒无事件时 yield SSE 注释行 `: heartbeat`，防止客户端/反向代理超时
- 客户端配合：使用 httpx.Timeout(connect=10, read=None, write=10, pool=10) 或依赖心跳重置读计时器

实现关键：
- StreamEmitter + asyncio.Queue 桥接同步 graph 与 async SSE
- contextvars.copy_context().run() 跨线程传递 current_emitter
- sentinel 标记 graph 执行完成
"""

import asyncio
import contextvars
import json
import os
from typing import Any, AsyncGenerator, Dict
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from starlette.responses import StreamingResponse

from src.api.deps import get_db_pool, get_session_manager, get_user_memory
from src.api.schemas import QueryRequest
from src.api.streaming import (
    StreamEmitter,
    current_emitter,
    current_user_memory,
    current_session_memory,
    current_fix_loop,
)
from src.graph.state import create_initial_state


def _should_write_session_turn(accumulated: Dict[str, Any]) -> bool:
    """判断本次请求是否应写入会话历史（fix-keyword-history-pollution）

    仅当非反问挂起且产出了最终 SQL 且非 SmartFix 失败时才写会话。
    拒答 / fail-fast 早退 / SmartFix 失败（即使有 final_sql）的轮次
    不入会话，避免无结果历史污染后续 follow-up 的关键词提取与理解。

    Args:
        accumulated: graph.stream 累积的最终 state 片段

    Returns:
        True 表示应调用 session.add_turn
    """
    if accumulated.get("__interrupted__"):
        return False
    return bool(accumulated.get("final_sql")) and not accumulated.get("fix_failed")

router = APIRouter()


_HEARTBEAT_INTERVAL = float(os.getenv("SSE_HEARTBEAT_INTERVAL", "15"))


def _serialize(obj: Any) -> Any:
    """递归将非可序列化对象转为 str"""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_serialize(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    return str(obj)


def _format_sse(event_type: str, data: Dict[str, Any]) -> str:
    """格式化为 SSE 文本行"""
    payload = json.dumps({"type": event_type, "data": data}, ensure_ascii=False)
    return f"data: {payload}\n\n"


def _with_qid(data: Dict[str, Any], query_id: str) -> Dict[str, Any]:
    """给 SSE 事件 payload 注入 query_id（若已存在则保留）。

    用于 event_stream 主循环里 emitter 之外手工构造的事件（result/error/done）。
    StreamEmitter.emit() 已自动注入；此函数补齐主循环里的事件路径。
    """
    if not query_id or "query_id" in data:
        return data
    return {**data, "query_id": query_id}


@router.post("/query")
async def query_endpoint(
    body: QueryRequest,
    pool=Depends(get_db_pool),
    session_manager=Depends(get_session_manager),
):
    """核心查询接口 — SSE 流式响应（决策 50）

    SSE 事件类型：
        stage           节点开始/结束     {node, status: started/done, ...}
        cache_check     历史命中检测      {hit, source, confidence, cached_sql}
        llm_thinking    qwen3 思考链片段  {node, text}
        keywords        IR 关键词提取    {groups}
        schema_recall   IR schema 召回   {groups}
        answerability   可回答性检查      {answerable, confidence, reason}
        sql_candidates  CG 候选 SQL     {candidates}
        execution       SQL 执行结果     {candidate_id, success, rows, error}
        final_decision  最终决策         {selected_id, selected_sql, reason}
        result          最终结果         {sql, result}
        error           错误             {error, node?}
        done            整条 query 完成  {has_result}

    所有事件 payload 都带 `query_id` 字段（§7b 决策，Q4=b 全量带）：
        前端可按 query_id 分组渲染、定位上下文。

    客户端 timeout 建议：
        httpx.Timeout(connect=10, read=None, write=10, pool=10)
    或保留有限 timeout，依赖每 15s 的 `: heartbeat\\n\\n` 重置读计时器。
    """
    # 0. 生成 query_id：单次请求的全局 ID（§7b 决策）
    query_id = uuid4().hex[:12]
    logger.info(
        f"[query_id={query_id}] 请求进入: "
        f"user={body.user_id} session={body.session_id} "
        f"db={body.db_id} query={body.query[:100]!r}"
    )

    # 1. 获取/创建会话
    session = session_manager.get_or_create_session(body.session_id, body.user_id)

    # 2. 获取用户记忆
    user_memory = get_user_memory(body.user_id)

    # 3. acquire DbContext（refcount += 1）
    try:
        db_ctx = pool.acquire(body.db_id)
    except FileNotFoundError as e:
        logger.warning(f"[query_id={query_id}] 数据库不存在: {body.db_id} ({e})")
        raise HTTPException(status_code=404, detail=f"数据库不存在: {body.db_id} ({e})")
    except Exception as e:
        logger.exception(f"[query_id={query_id}] 加载数据库失败: {body.db_id}")
        raise HTTPException(status_code=500, detail=f"加载数据库失败: {e}")

    # 4. 构建初始 state（resume 请求时不用 initial_state，改用 Command(resume=...)）
    is_resume = body.resume is not None
    initial_state = create_initial_state(
        user_query=body.query,
        user_id=body.user_id,
        database_filter=body.db_id,
        query_id=query_id,
    )
    recent_turns = session.get_recent_turns(n=5)
    initial_state["conversation_history"] = [t for t in recent_turns]
    initial_state["metric_definitions"] = user_memory.get_metric_definitions(min_confidence=0.7)
    # 决策 12：_user_memory / _session_memory 是 Python 对象实例，不能放进 state
    # （checkpointer 会序列化 state，对象不可 msgpack 序列化）。改用 ContextVar 传递，
    # 在 run_graph 里 set；节点通过 get_user_memory_ctx() / get_session_memory_ctx() 取。

    async def event_stream() -> AsyncGenerator[str, None]:
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()
        emitter = StreamEmitter(queue, loop, query_id=query_id)
        sentinel: Dict[str, Any] = {"__sentinel__": True}

        # 累积 graph.stream 的 update（用于事后构造 result/done 事件）
        accumulated: Dict[str, Any] = {}

        def run_graph() -> None:
            """在线程中执行 graph.stream，每个 update 推 graph_update 内部事件"""
            # §8.1.7：构造 LangSmith config（路径 A 之上的请求级追踪）
            #   - thread_id 让 LangSmith 按会话聚合（也是 LangGraph checkpoint 的 key）
            #   - run_name 让单次请求在 LangSmith UI 上可识别
            #   - tags / metadata 让多维度过滤成为可能
            config = {
                "configurable": {"thread_id": body.session_id},
                "run_name": f"query-{query_id}",
                "tags": [body.db_id, "api", f"user:{body.user_id}"],
                "metadata": {
                    "query_id": query_id,
                    "user_id": body.user_id,
                    "session_id": body.session_id,
                    "db_id": body.db_id,
                    "user_query": body.query[:200],
                },
            }
            token = current_emitter.set(emitter)
            um_token = current_user_memory.set(user_memory)
            sm_token = current_session_memory.set(session)
            fl_token = current_fix_loop.set(db_ctx.fix_loop) if db_ctx.fix_loop else None
            try:
                # 决策 12：resume 请求用 Command(resume=...) 恢复挂起的图；首次用 initial_state
                from langgraph.types import Command
                stream_input = Command(resume=body.resume) if is_resume else initial_state
                for update in db_ctx.graph.stream(stream_input, config=config):
                    # 检测反问中断（决策 12）：update 含 __interrupt__ → 推 clarification 事件
                    if "__interrupt__" in update:
                        interrupts = update["__interrupt__"]
                        if interrupts:
                            clarify_ctx = interrupts[0].value
                            emitter.emit("clarification", {
                                "question": clarify_ctx.get("question", "") if isinstance(clarify_ctx, dict) else str(clarify_ctx),
                                "ambiguities": clarify_ctx.get("ambiguities", []) if isinstance(clarify_ctx, dict) else [],
                                "round": clarify_ctx.get("round", 1) if isinstance(clarify_ctx, dict) else 1,
                                "awaiting_answer": True,
                            })
                        accumulated["__interrupted__"] = True
                        continue
                    # 累积到本地 state 字典（_wrap_node 已发 stage 事件，这里只攒结果）
                    for _, node_output in update.items():
                        if isinstance(node_output, dict):
                            accumulated.update(node_output)
            except Exception as e:
                logger.exception(f"[query_id={query_id}] graph.stream 异常")
                emitter.emit("error", {"error": str(e)})
            finally:
                current_emitter.reset(token)
                current_user_memory.reset(um_token)
                current_session_memory.reset(sm_token)
                if fl_token is not None:
                    current_fix_loop.reset(fl_token)
                # sentinel 表示线程结束
                try:
                    loop.call_soon_threadsafe(queue.put_nowait, sentinel)
                except RuntimeError:
                    pass

        # 跨线程传递 contextvar：用 copy_context().run 包裹
        ctx = contextvars.copy_context()

        def run_graph_in_ctx():
            ctx.run(run_graph)

        # 启动后台线程
        task = loop.run_in_executor(None, run_graph_in_ctx)

        try:
            while True:
                try:
                    evt = await asyncio.wait_for(
                        queue.get(), timeout=_HEARTBEAT_INTERVAL
                    )
                except asyncio.TimeoutError:
                    # 心跳：SSE 注释行
                    yield ": heartbeat\n\n"
                    continue

                if evt is sentinel:
                    break

                # 普通事件
                try:
                    yield _format_sse(evt["type"], evt.get("data", {}))
                except Exception as e:
                    logger.warning(f"序列化 SSE 事件失败: {e}, evt={evt}")

            # 等待后台任务结束（拿异常）
            try:
                await task
            except Exception as e:
                yield _format_sse("error", _with_qid({"error": str(e)}, query_id))

            # 推送最终 result（反问挂起时不推 result/rejection，前端已收 clarification 事件，等 resume）
            if accumulated.get("__interrupted__"):
                logger.info(f"[query_id={query_id}] 反问挂起，等待用户回答（resume）")
            else:
                rejection = accumulated.get("rejection_reason")
                if rejection:
                    yield _format_sse("error", _with_qid({"error": rejection, "rejection": True}, query_id))
                elif accumulated.get("final_sql"):
                    yield _format_sse("result", _with_qid({
                        "sql": accumulated["final_sql"],
                        "result": _serialize(accumulated.get("final_result")),
                    }, query_id))

            # 更新会话历史
            # 更新会话历史（反问挂起时跳过，等 resume 完成后再写）
            # fix-keyword-history-pollution：拒答/未产出 final_sql 的轮次不入会话，
            # 避免无结果历史污染后续 follow-up 的关键词提取与理解
            if _should_write_session_turn(accumulated):
                try:
                    turn_data = {
                        "user_query": body.query,
                        "final_sql": accumulated.get("final_sql", ""),
                        "cache_hit": accumulated.get("cache_hit", False),
                        "db_id": body.db_id,
                    }
                    if accumulated.get("error"):
                        turn_data["error"] = accumulated["error"]
                    if accumulated.get("rejection_reason"):
                        turn_data["rejection_reason"] = accumulated["rejection_reason"]
                    final_result = accumulated.get("final_result")
                    if isinstance(final_result, (list, tuple)) and final_result:
                        first = final_result[0]
                        columns = list(first.keys()) if hasattr(first, "keys") else []
                        turn_data["result_meta"] = {
                            "row_count": len(final_result),
                            "columns": columns,
                        }
                    session.add_turn(turn_data)
                except Exception as e:
                    logger.warning(f"[query_id={query_id}] 更新会话历史失败: {e}")

            done_payload = _with_qid({
                "has_result": bool(accumulated.get("final_sql")),
                # 决策 12：反问挂起标记，前端据此等待用户回答后发 resume 请求
                "awaiting_clarification": bool(accumulated.get("__interrupted__")),
                # 决策 51：暴露失败标记、决策路径、修复轮次给前端
                "fix_failed": accumulated.get("fix_failed", False),
                "decision_path": accumulated.get("decision_path", ""),
                "fix_rounds_used": accumulated.get("fix_rounds_used", 0),
                "last_error": accumulated.get("last_error"),
            }, query_id)
            yield _format_sse("done", done_payload)

            logger.info(
                f"[query_id={query_id}] 完成: "
                f"has_result={done_payload['has_result']} "
                f"fix_failed={done_payload['fix_failed']} "
                f"decision_path={done_payload['decision_path']!r}"
            )

        finally:
            # 无论如何 release（refcount -= 1）
            try:
                pool.release(body.db_id)
            except Exception:
                pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

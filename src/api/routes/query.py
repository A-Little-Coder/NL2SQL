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
from typing import Any, AsyncGenerator, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from starlette.responses import StreamingResponse

from src.api.deps import get_db_pool, get_session_manager, get_user_memory
from src.api.schemas import QueryRequest
from src.api.streaming import StreamEmitter, current_emitter
from src.graph.state import create_initial_state

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

    客户端 timeout 建议：
        httpx.Timeout(connect=10, read=None, write=10, pool=10)
    或保留有限 timeout，依赖每 15s 的 `: heartbeat\\n\\n` 重置读计时器。
    """
    # 1. 获取/创建会话
    session = session_manager.get_or_create_session(body.session_id, body.user_id)

    # 2. 获取用户记忆
    user_memory = get_user_memory(body.user_id)

    # 3. acquire DbContext（refcount += 1）
    try:
        db_ctx = pool.acquire(body.db_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=f"数据库不存在: {body.db_id} ({e})")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"加载数据库失败: {e}")

    # 4. 构建初始 state
    initial_state = create_initial_state(
        user_query=body.query,
        user_id=body.user_id,
        database_filter=body.db_id,
    )
    recent_turns = session.get_recent_turns(n=5)
    initial_state["conversation_history"] = [t for t in recent_turns]
    initial_state["metric_definitions"] = user_memory.get_metric_definitions(min_confidence=0.7)
    initial_state["_user_memory"] = user_memory
    initial_state["_session_memory"] = session

    async def event_stream() -> AsyncGenerator[str, None]:
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()
        emitter = StreamEmitter(queue, loop)
        sentinel: Dict[str, Any] = {"__sentinel__": True}

        # 累积 graph.stream 的 update（用于事后构造 result/done 事件）
        accumulated: Dict[str, Any] = {}

        def run_graph() -> None:
            """在线程中执行 graph.stream，每个 update 推 graph_update 内部事件"""
            token = current_emitter.set(emitter)
            try:
                for update in db_ctx.graph.stream(initial_state):
                    # 累积到本地 state 字典（_wrap_node 已发 stage 事件，这里只攒结果）
                    for _, node_output in update.items():
                        if isinstance(node_output, dict):
                            accumulated.update(node_output)
            except Exception as e:
                logger.exception("graph.stream 异常")
                emitter.emit("error", {"error": str(e)})
            finally:
                current_emitter.reset(token)
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
                yield _format_sse("error", {"error": str(e)})

            # 推送最终 result
            rejection = accumulated.get("rejection_reason")
            if rejection:
                yield _format_sse("error", {"error": rejection, "rejection": True})
            elif accumulated.get("final_sql"):
                yield _format_sse("result", {
                    "sql": accumulated["final_sql"],
                    "result": _serialize(accumulated.get("final_result")),
                })

            # 更新会话历史
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
                logger.warning(f"更新会话历史失败: {e}")

            yield _format_sse("done", {
                "has_result": bool(accumulated.get("final_sql")),
            })

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

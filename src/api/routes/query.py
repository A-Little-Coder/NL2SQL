"""核心查询接口

POST /api/v1/query — SSE 流式响应
"""

import asyncio
import json
from typing import Any, AsyncGenerator, Dict, List, Tuple

from fastapi import APIRouter, Depends
from starlette.responses import StreamingResponse

from src.api.deps import get_graph, get_session_manager, get_user_memory
from src.api.schemas import QueryRequest
from src.graph.state import create_initial_state

router = APIRouter()


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


def _run_stream(graph, initial_state) -> List[Tuple[str, Dict[str, Any]]]:
    """在同步上下文中运行 graph.stream()，收集所有节点输出"""
    results = []
    # stream_mode="updates" 返回 {node_name: update_dict}
    for update in graph.stream(initial_state):
        results.append(update)
    return results


def _accumulate_state(stream_results: List[Dict]) -> Dict[str, Any]:
    """累积所有节点输出为完整 state"""
    state = {}
    for update in stream_results:
        # stream_mode="updates" 返回 {node_name: {field: value}}
        for node_name, node_output in update.items():
            if isinstance(node_output, dict):
                state.update(node_output)
    return state


@router.post("/query")
async def query_endpoint(
    body: QueryRequest,
    graph=Depends(get_graph),
    session_manager=Depends(get_session_manager),
):
    """
    核心查询接口 — SSE 流式响应
    """
    # 1. 获取或创建会话
    session = session_manager.get_or_create_session(body.session_id, body.user_id)

    # 2. 获取用户记忆
    user_memory = get_user_memory(body.user_id)

    # 3. 构建初始 State
    initial_state = create_initial_state(
        user_query=body.query,
        user_id=body.user_id,
    )
    recent_turns = session.get_recent_turns(n=5)
    initial_state["conversation_history"] = [t for t in recent_turns]
    initial_state["metric_definitions"] = user_memory.get_metric_definitions(min_confidence=0.7)
    initial_state["_user_memory"] = user_memory
    initial_state["_session_memory"] = session

    async def event_stream():
        try:
            loop = asyncio.get_event_loop()
            stream_results = await loop.run_in_executor(None, _run_stream, graph, initial_state)

            # 遍历节点输出，生成 SSE 事件
            seen_cache = False
            for update in stream_results:
                for node_name, node_output in update.items():
                    if not isinstance(node_output, dict):
                        continue

                    # 阶段事件
                    yield _format_sse("stage", {"node": node_name, "status": "done"})

                    # history_cache 事件
                    if node_name == "history_cache" and not seen_cache:
                        seen_cache = True
                        yield _format_sse("cache_check", {
                            "hit": node_output.get("cache_hit", False),
                            "source": node_output.get("cache_source"),
                            "confidence": node_output.get("cache_confidence", 0.0),
                            "cached_sql": node_output.get("cached_sql"),
                        })

                    # 错误事件
                    if node_output.get("error"):
                        yield _format_sse("error", {
                            "node": node_name,
                            "error": node_output["error"],
                        })

            # 累积最终 state
            final_state = _accumulate_state(stream_results)
            rejection = final_state.get("rejection_reason")

            if rejection:
                yield _format_sse("error", {"error": rejection, "rejection": True})
            elif final_state.get("final_sql"):
                yield _format_sse("result", {
                    "sql": final_state["final_sql"],
                    "result": _serialize(final_state.get("final_result")),
                })

            # 更新会话记忆
            turn_data = {
                "user_query": body.query,
                "final_sql": final_state.get("final_sql", ""),
                "final_result": final_state.get("final_result"),
                "cache_hit": final_state.get("cache_hit", False),
                "error": final_state.get("error"),
                "rejection_reason": final_state.get("rejection_reason"),
            }
            session.add_turn(turn_data)

            yield _format_sse("done", {
                "has_result": bool(final_state.get("final_sql")),
            })

        except Exception as e:
            yield _format_sse("error", {"error": str(e)})
            yield _format_sse("done", {"has_result": False})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

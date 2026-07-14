"""会话管理接口

- POST /api/v1/sessions - 显式创建新会话（决策 49）
- GET /api/v1/sessions - 列出用户会话（?user_id=xxx[&page=0&size=20]，按 created_at 分片）
- GET /api/v1/sessions/{session_id}/history - 获取对话历史（优先 event_cache 事件流，回落 session_memory 摘要）
- DELETE /api/v1/sessions/{session_id} - 删除会话

change session-restore-event-cache：
- create_session 双写 session_memory（复用层）+ event_cache（展示层 index 登记）
- list_sessions 分页化，读 event_cache index（每页 ≤20 会话，最新 shard 在前）
- get_session_history 优先返回 event_cache 事件流（前端 reduceSseEvent 重放），
  无事件流则回落 session_memory 摘要（老会话兼容）
- delete_session 同步清理 event_cache
"""

from fastapi import APIRouter, Depends, Query
from loguru import logger

from src.api.deps import get_event_cache, get_session_manager
from src.api.schemas import (
    CreateSessionRequest,
    CreateSessionResponse,
    ErrorResponse,
    SessionHistoryResponse,
    SessionListPageResponse,
    SessionSummary,
)

router = APIRouter()


@router.post("/sessions", response_model=CreateSessionResponse)
async def create_session(
    body: CreateSessionRequest,
    session_manager=Depends(get_session_manager),
    event_cache=Depends(get_event_cache),
):
    """显式创建新会话（决策 49）

    双写：session_memory 建会话（复用层）+ event_cache 登记展示索引（展示层）。
    body.db_id 当前仅作记录，不在 SessionManager 内强制绑定；
    实际查询时通过 POST /query 的 db_id 字段选择数据库。
    """
    session = session_manager.create_session(user_id=body.user_id)
    # D6: 双写 event_cache 索引（展示层列表分页用）
    try:
        event_cache.register_session(body.user_id, session.session_id)
    except Exception as e:
        logger.warning(f"event_cache 登记会话失败 ({session.session_id}): {e}")
    return CreateSessionResponse(
        session_id=session.session_id,
        user_id=body.user_id,
    )


@router.get("/sessions", response_model=SessionListPageResponse)
async def list_sessions(
    user_id: str = Query(..., description="用户标识"),
    page: int = Query(0, ge=0, description="页码（0=最新 shard，按 created_at 分片倒序）"),
    size: int = Query(20, ge=1, le=20, description="每页会话数（=shard 大小，≤20）"),
    session_manager=Depends(get_session_manager),
    event_cache=Depends(get_event_cache),
):
    """列出用户会话（按 created_at 分片，最新 shard 在前）。

    读 event_cache index 分页返回。session_manager 不再服务前端列表（仅复用层用）。
    event_cache 异常时回落 session_memory 全量（has_more=False）。
    """
    try:
        result = event_cache.list_sessions_paged(user_id, page=page, size=size)
        sessions = [SessionSummary(**s) for s in result["sessions"]]
        return SessionListPageResponse(
            user_id=user_id,
            page=result["page"],
            size=result["size"],
            has_more=result["has_more"],
            sessions=sessions,
        )
    except Exception as e:
        logger.warning(f"event_cache list_sessions_paged 失败，回落 session_memory: {e}")
        sessions = [SessionSummary(**s) for s in session_manager.list_sessions(user_id)]
        return SessionListPageResponse(
            user_id=user_id, page=page, size=size, has_more=False, sessions=sessions
        )


@router.get("/sessions/{session_id}/history")
async def get_session_history(
    session_id: str,
    user_id: str = Query(..., description="用户标识"),
    session_manager=Depends(get_session_manager),
    event_cache=Depends(get_event_cache),
):
    """获取指定会话的对话历史。

    优先返回 event_cache 事件流（source="events"，前端 reduceSseEvent 重放还原完整 Turn）；
    无事件流则回落 session_memory 摘要（source="summary"，老会话兼容，前端简化重建）。
    """
    # 优先 event_cache 事件流
    try:
        events_data = event_cache.get_session_events(user_id, session_id)
    except Exception as e:
        logger.warning(f"event_cache get_session_events 失败 ({session_id}): {e}")
        events_data = None
    if events_data is not None and events_data.get("has_events"):
        return SessionHistoryResponse(
            session_id=session_id,
            turns=events_data["turns"],
            source="events",
            has_events=True,
        )
    # 回落 session_memory 摘要（老会话）
    session = session_manager.get_session(session_id, user_id)
    if session is None:
        return ErrorResponse(error="会话不存在", detail=f"session_id={session_id}")
    turns = []
    for i in range(session.get_turn_count()):
        turn = session.get_recent_turns(n=session.get_turn_count())[i]
        turns.append(turn)
    return SessionHistoryResponse(
        session_id=session_id,
        turns=turns,
        source="summary",
        has_events=False,
    )


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    user_id: str = Query(..., description="用户标识"),
    session_manager=Depends(get_session_manager),
    event_cache=Depends(get_event_cache),
):
    """删除指定会话（同步清理 session_memory 复用层 + event_cache 展示层）。"""
    deleted = session_manager.delete_session(session_id, user_id)
    if not deleted:
        return ErrorResponse(error="会话不存在", detail=f"session_id={session_id}")
    # 同步清理 event_cache（index + shard 文件）
    try:
        event_cache.delete_session(user_id, session_id)
    except Exception as e:
        logger.warning(f"event_cache 删除会话失败 ({session_id}): {e}")
    return {"status": "deleted", "session_id": session_id}

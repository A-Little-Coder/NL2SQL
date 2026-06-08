"""会话管理接口

- GET /api/v1/sessions — 列出用户会话（?user_id=xxx）
- GET /api/v1/sessions/{session_id}/history — 获取对话历史
- DELETE /api/v1/sessions/{session_id} — 删除会话
"""

from fastapi import APIRouter, Depends, Query

from src.api.deps import get_session_manager
from src.api.schemas import ErrorResponse, SessionHistoryResponse, SessionSummary

router = APIRouter()


@router.get("/sessions")
async def list_sessions(
    user_id: str = Query(..., description="用户标识"),
    session_manager=Depends(get_session_manager),
):
    """列出用户的所有会话（按更新时间降序）"""
    sessions = session_manager.list_sessions(user_id)
    return {
        "user_id": user_id,
        "sessions": [SessionSummary(**s) for s in sessions],
    }


@router.get("/sessions/{session_id}/history")
async def get_session_history(
    session_id: str,
    user_id: str = Query(..., description="用户标识"),
    session_manager=Depends(get_session_manager),
):
    """获取指定会话的完整对话历史"""
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
    )


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    user_id: str = Query(..., description="用户标识"),
    session_manager=Depends(get_session_manager),
):
    """删除指定会话"""
    deleted = session_manager.delete_session(session_id, user_id)
    if not deleted:
        return ErrorResponse(error="会话不存在", detail=f"session_id={session_id}")
    return {"status": "deleted", "session_id": session_id}

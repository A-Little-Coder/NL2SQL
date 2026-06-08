"""用户记忆接口

- GET /api/v1/users/{user_id}/memory — 获取完整用户记忆
- GET /api/v1/users/{user_id}/metrics — 获取指标定义
"""

from fastapi import APIRouter, Depends

from src.api.deps import get_user_memory
from src.api.schemas import MetricDefinitionResponse, UserMemoryResponse

router = APIRouter()


@router.get("/users/{user_id}/memory")
async def get_user_memory_endpoint(user_id: str):
    """获取指定用户的完整记忆"""
    um = get_user_memory(user_id)
    return UserMemoryResponse(
        user_id=user_id,
        term_preferences=um._data.get("term_preferences", {}),
        frequently_used_tables=um._data.get("frequently_used_tables", {}),
        metric_definitions=um._data.get("metric_definitions", {}),
        query_preferences=um.get_query_preferences(),
        domain_context=um.get_domain_context(),
        clarification_history=um._data.get("clarification_history", []),
    )


@router.get("/users/{user_id}/metrics")
async def get_user_metrics(
    user_id: str,
):
    """获取指定用户的指标定义"""
    um = get_user_memory(user_id)
    metrics = um.get_metric_definitions(min_confidence=0.0)
    return MetricDefinitionResponse(
        user_id=user_id,
        metrics=metrics,
    )

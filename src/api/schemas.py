"""API Pydantic 请求/响应模型"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    """查询请求"""
    query: str = Field(..., description="用户自然语言查询")
    session_id: str = Field(..., description="会话 ID")
    user_id: str = Field(default="default", description="用户标识")


class SSEEvent(BaseModel):
    """SSE 事件"""
    type: str = Field(..., description="事件类型")
    data: Optional[Any] = Field(None, description="事件数据")


class SessionSummary(BaseModel):
    """会话摘要"""
    session_id: str
    created_at: str
    updated_at: str
    status: str = "active"
    turn_count: int = 0


class SessionHistoryResponse(BaseModel):
    """会话历史响应"""
    session_id: str
    turns: List[Dict[str, Any]]


class UserMemoryResponse(BaseModel):
    """用户记忆响应"""
    user_id: str
    term_preferences: Dict[str, Any]
    frequently_used_tables: Dict[str, Any]
    metric_definitions: Dict[str, Any]
    query_preferences: Dict[str, Any]
    domain_context: Dict[str, Any]
    clarification_history: List[Dict[str, Any]]


class MetricDefinitionResponse(BaseModel):
    """指标定义列表响应"""
    user_id: str
    metrics: List[Dict[str, Any]]


class ErrorResponse(BaseModel):
    """错误响应"""
    error: str
    detail: Optional[str] = None

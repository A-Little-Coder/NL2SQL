"""API Pydantic 请求/响应模型"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    """查询请求"""
    query: str = Field(..., description="用户自然语言查询")
    session_id: str = Field(..., description="会话 ID")
    user_id: str = Field(default="default", description="用户标识")
    db_id: str = Field(..., min_length=1, description="数据库 ID（决策 49，决定使用哪套数据库资源）")


class CreateSessionRequest(BaseModel):
    """显式创建会话的请求（决策 49）"""
    user_id: str = Field(..., description="用户标识")
    db_id: Optional[str] = Field(default=None, description="可选：会话关联的数据库 id")


class CreateSessionResponse(BaseModel):
    """显式创建会话的响应"""
    session_id: str
    user_id: str


class DatabaseInfo(BaseModel):
    """数据库摘要（GET /databases）"""
    db_id: str
    db_path: str


class DatabaseListResponse(BaseModel):
    """数据库列表响应"""
    databases: List[DatabaseInfo]


class TableListResponse(BaseModel):
    """表清单响应"""
    db_id: str
    tables: List[str]


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

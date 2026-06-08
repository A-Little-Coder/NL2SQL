"""
NL2SQL 主图状态定义

依据 决策 22 / §18.1：
- 主图 State 采用 TypedDict，字段覆盖整条主链路所需的中间产物
- 子图（IR、SS、CG、Execution、Decision）的内部状态另行定义，
  但子图的输入/输出字段应与本 State 字段保持一致或可映射

注意：LangGraph 在每个节点返回 dict 后会做浅合并；List/Dict 类型字段
若需追加，请在节点中先复制再追加再返回，以避免就地修改导致 trace 不准。
"""

from typing import Any, Dict, List, Optional, TypedDict


class NL2SQLState(TypedDict, total=False):
    """
    主图共享状态

    Fields:
        # ===== 用户输入 =====
        user_query: 用户原始自然语言查询
        user_id: 用户标识（用于 UserMemory，Phase 2 启用）
        database_filter: 限定检索的数据库 db_id（可空）

        # ===== IR 产出 =====
        keywords: LLM 提取的关键词列表
        retrieved_context: RetrievedContext 实例（IR 综合结果）

        # ===== Clarification 产出（Phase 2，本期占位）=====
        clarification_count: 已发起的反问次数
        clarification_history: 反问历史
        clarified_keywords: 经澄清后的关键词
        clarification_done: 是否结束反问流程

        # ===== SS 产出 =====
        selected_schema: List[MSchemaTable] 经裁剪后的 schema

        # ===== CG 产出 =====
        sql_candidates: List[SQLCandidate] 候选 SQL

        # ===== Execution 产出 =====
        execution_results: 各候选执行结果（直接更新到 sql_candidates 内部字段）
        schema_text: 给 LLM 修复用的 schema 文本（避免每次重生成）

        # ===== Decision 产出 =====
        final_decision: DecisionResult 最终决策结果
        final_sql: 选定的 SQL
        final_result: 选定 SQL 的执行结果

        # ===== 辅助 =====
        error: 主图级别错误信息
        trace_log: 各节点产生的轨迹日志（便于调试）

        # ===== 会话历史（由 API 层/调用方注入）=====
        conversation_history: 当前会话的历史对话轮次
        cache_hit: 历史命中标记
        cached_sql: 命中的缓存 SQL
        cache_source: 命中来源
        cache_confidence: 命中置信度
        metric_definitions: 用户记忆中的指标定义（由 API 层注入）

        # ===== 内部注入（API 层注入，graph 节点内部使用）=====
        _user_memory: Any  # UserMemory 实例
        _session_memory: Any  # SessionMemory 实例
    """

    # ===== 用户输入 =====
    user_query: str
    user_id: str
    database_filter: Optional[str]

    # ===== IR =====
    keywords: List[str]
    retrieved_context: Any  # RetrievedContext

    # ===== Clarification =====
    clarification_count: int
    clarification_history: List[Dict[str, Any]]
    clarified_keywords: List[str]
    clarification_done: bool

    # ===== SS =====
    selected_schema: List[Any]  # List[MSchemaTable]

    # ===== CG =====
    sql_candidates: List[Any]  # List[SQLCandidate]

    # ===== Execution =====
    execution_results: List[Any]
    schema_text: str

    # ===== Decision =====
    final_decision: Any  # DecisionResult
    final_sql: str
    final_result: Any

    # ===== Verification（决策 23/24）=====
    answerability_result: Optional[Dict[str, Any]]  # 可回答性检查结果
    result_verification: Optional[Dict[str, Any]]    # 结果可信度验证结果
    rejection_reason: Optional[str]                   # 拒答原因

    # ===== 会话历史（由 API 层/调用方注入）=====
    conversation_history: List[Dict[str, Any]]
    cache_hit: bool
    cached_sql: Optional[str]
    cache_source: Optional[str]
    cache_confidence: float
    metric_definitions: List[Dict[str, Any]]

    # ===== 内部注入 =====
    _user_memory: Any
    _session_memory: Any

    # ===== 辅助 =====
    error: Optional[str]
    trace_log: List[str]


def create_initial_state(
    user_query: str,
    user_id: str = "default",
    database_filter: Optional[str] = None,
) -> NL2SQLState:
    """
    构造一个完备的初始 State

    Args:
        user_query: 用户原始查询
        user_id: 用户标识，默认 "default"
        database_filter: 可选数据库限定

    Returns:
        NL2SQLState: 已用默认空值填充所有字段的初始状态
    """
    return NL2SQLState(
        user_query=user_query,
        user_id=user_id,
        database_filter=database_filter,
        keywords=[],
        retrieved_context=None,
        clarification_count=0,
        clarification_history=[],
        clarified_keywords=[],
        clarification_done=True,  # Phase 1 默认跳过反问
        selected_schema=[],
        sql_candidates=[],
        execution_results=[],
        schema_text="",
        final_decision=None,
        final_sql="",
        final_result=None,
        answerability_result=None,
        result_verification=None,
        rejection_reason=None,
        conversation_history=[],
        cache_hit=False,
        cached_sql=None,
        cache_source=None,
        cache_confidence=0.0,
        metric_definitions=[],
        _user_memory=None,
        _session_memory=None,
        error=None,
        trace_log=[],
    )

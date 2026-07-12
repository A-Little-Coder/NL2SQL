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

        # ===== Rewrite 改写环节（rewrite-before-cache v2）=====
        rewritten_query: 改写后的完整查询（空字符串表示未改写/无需改写）
        rewrite_round: 改写轮次（0=未改写，1=第一次改写，2=第二次改写）
        rewrite_rejection_reason: 前置拒答检测拒答原因
        rewrite_reason: 改写说明
        clarify_context: 用户补充信息（反问回答，Rewrite 子图用）

        # ===== IR 产出 =====
        keywords: LLM 提取的关键词列表
        retrieved_context: RetrievedContext 实例（IR 综合结果）

        # ===== Clarification 产出（Phase 2，本期占位）=====
        clarification_count: 已发起的反问次数
        clarification_history: 反问历史
        clarified_keywords: 经澄清后的关键词
        clarification_done: 是否结束反问流程

        # ===== TaskPlanner / 反问机制（决策 9-15）=====
        plan_result: TaskPlanner 三选一裁决输出（verdict/subqueries/ambiguities...）
        subqueries: 多意图分解后的子查询列表
        subquery_results: 每个子查询的最终结果（含 final_decision / decision_path）
        clarify_round: 反问轮次计数（存 state，checkpoint 持久化）
        clarify_question: 当前要问用户的问题（interrupt 用）
        summary_text: 总结模块（aggregate_results）输出

        # ===== SS 产出 =====
        selected_schema: List[MSchemaTable] 经裁剪后的 schema
        # ===== SchemaFinalize 产出（relocate-join-path-injection）=====
        join_paths_text: 表关联格式化文本（SS 之后计算，供 CG/SmartFix 双消费）

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
        conversation_history: 当前会话最近轮次（由 API 层注入，最多5轮）
        cache_hit: 历史命中标记
        cached_sql: 命中的缓存 SQL
        cache_source: 命中来源
        cache_confidence: 命中置信度
        metric_definitions: 用户记忆中的指标定义（由 API 层注入）
        historical_sql_refs: HistoryCache 不复用时保留的历史 query/sql 弱参考

        # ===== 缓存复用增强（harden-history-cache）=====
        cached_historical_query: 命中的历史 query（供 value_rewrite 使用）
        adjusted_cached_sql: 经值参数改写后的 cached_sql
        cache_confirm_approved: 用户是否确认复用（True=复用，False=重新生成）

        # ===== 内部注入（API 层注入，graph 节点内部使用）=====
        _user_memory: Any  # UserMemory 实例
        _session_memory: Any  # SessionMemory 实例
    """

    # ===== 用户输入 =====
    user_query: str
    user_id: str
    database_filter: Optional[str]
    query_id: str  # 单次请求的全局 ID（uuid4().hex[:12]，由 API 层生成；离线/CLI 可留空）

    # ===== Rewrite 改写环节（rewrite-before-cache v2）=====
    rewritten_query: str           # 改写后的完整查询（空字符串表示未改写/无需改写）
    rewrite_round: int             # 改写轮次（0=未改写，1=第一次改写，2=第二次改写）
    rewrite_reason: str            # 改写说明
    rewrite_rejection_reason: Optional[str]  # 前置拒答检测拒答原因
    pre_reject_category: Optional[str]      # 前置拒答 LLM 判定类别（write_op/dangerous_info/normal）
    clarify_context: Optional[str]   # 用户补充信息（反问回答，Rewrite 子图用）

    # ===== IR =====
    keywords: List[str]
    retrieved_context: Any  # RetrievedContext

    # ===== Clarification =====
    clarification_count: int
    clarification_history: List[Dict[str, Any]]
    clarified_keywords: List[str]
    clarification_done: bool
    # ===== TaskPlanner / 反问机制（决策 9-15，2026-06-29）=====
    plan_result: Dict[str, Any]              # TaskPlanner 输出（verdict/subqueries/ambiguities...）
    subqueries: List[str]                    # 分解后的子查询列表
    subquery_results: List[Dict[str, Any]]   # 每个子查询的最终结果
    clarify_round: int                       # 反问轮次计数（checkpoint 持久化）
    clarify_question: str                    # 当前要问用户的问题（interrupt 用）
    summary_text: str                        # 总结模块输出（aggregate_results）

    # ===== SS =====
    selected_schema: List[Any]  # List[MSchemaTable]
    # ===== SchemaFinalize（relocate-join-path-injection）=====
    join_paths_text: str  # 表关联格式化文本，供 CG 生成 Prompt + SmartFix 修复 Prompt 双消费

    # ===== CG =====
    sql_candidates: List[Any]  # List[SQLCandidate]

    # ===== Execution =====
    execution_results: List[Any]
    schema_text: str

    # ===== Decision =====
    final_decision: Any  # DecisionResult
    final_sql: str
    final_result: Any

    # ===== 评分阶段（决策 51：两段式评分 + 单候选修复） =====
    candidate_scores_r1: List[Dict[str, Any]]            # R1 数据视角评分 [{id, score, reason}]
    candidate_scores_r2: Optional[List[Dict[str, Any]]]  # R2 SQL 视角评分（仅 R1 并列=5 时触发）
    selected_candidate_id: Optional[str]                  # 进入 SmartFix 的候选 ID

    # ===== SmartFix 阶段 =====
    fix_failed: bool                                       # SmartFix 3 轮全部失败标记
    fix_rounds_used: int                                   # 实际使用的修复轮次
    last_error: Optional[str]                              # 失败时的最后错误信息
    fix_history: List[Dict[str, Any]]                      # 修复历史 [{round, sql, error}]
    decision_path: str                                     # 决策路径标识 (A/B/C/D/E/F/G/H)

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
    historical_sql_refs: List[Dict[str, Any]]  # 不可复用历史的 query/sql 弱参考

    # ===== 缓存复用增强（harden-history-cache）=====
    cached_historical_query: Optional[str]  # 命中的历史 query（供 value_rewrite 使用）
    adjusted_cached_sql: Optional[str]  # 经值参数改写后的 cached_sql
    cache_confirm_approved: Optional[bool]  # 用户是否确认复用（True=复用，False=重新生成）

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
    query_id: str = "",
) -> NL2SQLState:
    """
    构造一个完备的初始 State

    Args:
        user_query: 用户原始查询
        user_id: 用户标识，默认 "default"
        database_filter: 可选数据库限定
        query_id: 单次请求的全局 ID（API 层生成；CLI/离线场景可留空）

    Returns:
        NL2SQLState: 已用默认空值填充所有字段的初始状态
    """
    return NL2SQLState(
        user_query=user_query,
        user_id=user_id,
        database_filter=database_filter,
        query_id=query_id,
        rewritten_query="",
        rewrite_round=0,
        rewrite_reason="",
        rewrite_rejection_reason=None,
        pre_reject_category=None,
        clarify_context=None,
        keywords=[],
        retrieved_context=None,
        clarification_count=0,
        clarification_history=[],
        clarified_keywords=[],
        clarification_done=True,  # Phase 1 默认跳过反问
        plan_result={},
        subqueries=[],
        subquery_results=[],
        clarify_round=0,
        clarify_question="",
        summary_text="",
        selected_schema=[],
        join_paths_text="",
        sql_candidates=[],
        execution_results=[],
        schema_text="",
        final_decision=None,
        final_sql="",
        final_result=None,
        candidate_scores_r1=[],
        candidate_scores_r2=None,
        selected_candidate_id=None,
        fix_failed=False,
        fix_rounds_used=0,
        last_error=None,
        fix_history=[],
        decision_path="",
        answerability_result=None,
        result_verification=None,
        rejection_reason=None,
        conversation_history=[],
        cache_hit=False,
        cached_sql=None,
        cache_source=None,
        cache_confidence=0.0,
        metric_definitions=[],
        historical_sql_refs=[],
        cached_historical_query=None,
        adjusted_cached_sql=None,
        cache_confirm_approved=None,
        _user_memory=None,
        _session_memory=None,
        error=None,
        trace_log=[],
    )

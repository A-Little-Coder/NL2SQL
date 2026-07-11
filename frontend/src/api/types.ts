/**
 * API 契约层 —— 单一契约源（决策 D1/D9）
 *
 * 本文件镜像后端 Pydantic schema（src/api/schemas.py）与 SSE 事件契约
 * （src/api/routes/query.py 文档），是前端唯一的类型定义源。后端字段变动
 * 时只需同步本文件，TypeScript 编译期即可拦下事件字段错配。
 *
 * SSE 线格式：`data: {"type": "<event_type>", "data": {<payload>}}\n\n`
 * 心跳行：`: heartbeat\n\n`（不产生可见事件，仅重置客户端读超时）
 * 所有事件 payload 都带 `query_id` 字段（§7b 决策）。
 */

// ============================================================================
// REST 请求/响应模型（镜像 src/api/schemas.py）
// ============================================================================

/** 查询请求（POST /api/v1/query body）。resume 非 null 时表示反问恢复请求。 */
export interface QueryRequest {
  /** 用户自然语言查询（resume 请求时可留空） */
  query: string;
  /** 会话 ID */
  session_id: string;
  /** 用户标识，默认 "default" */
  user_id: string;
  /** 数据库 ID（决策 49，决定使用哪套数据库资源） */
  db_id: string;
  /** 反问恢复：用户对上一轮 clarification 的回答。非 null 即 resume 请求 */
  resume?: string | null;
}

/** 显式创建会话请求（POST /api/v1/sessions） */
export interface CreateSessionRequest {
  user_id: string;
  /** 可选：会话关联的数据库 id（当前仅作记录） */
  db_id?: string | null;
}

/** 显式创建会话响应 */
export interface CreateSessionResponse {
  session_id: string;
  user_id: string;
}

/** 数据库摘要（GET /api/v1/databases） */
export interface DatabaseInfo {
  db_id: string;
  db_path: string;
}

/** 数据库列表响应 */
export interface DatabaseListResponse {
  databases: DatabaseInfo[];
}

/** 表清单响应（GET /api/v1/databases/{db_id}/tables） */
export interface TableListResponse {
  db_id: string;
  tables: string[];
}

/** 会话摘要 */
export interface SessionSummary {
  session_id: string;
  created_at: string;
  updated_at: string;
  status: string;
  turn_count: number;
}

/** 会话列表响应（GET /api/v1/sessions?user_id=xxx） */
export interface SessionListResponse {
  user_id: string;
  sessions: SessionSummary[];
}

/**
 * 会话历史单轮（GET /api/v1/sessions/{id}/history 中的 turns 元素）
 *
 * 后端 turn_data 结构（见 query.py session.add_turn）：宽松类型，字段非全部必填。
 */
export interface SessionTurn {
  user_query?: string;
  final_sql?: string;
  cache_hit?: boolean;
  db_id?: string;
  error?: string;
  rejection_reason?: string;
  result_meta?: { row_count: number; columns: string[] };
  timestamp?: string;
  [key: string]: unknown;
}

/** 会话历史响应 */
export interface SessionHistoryResponse {
  session_id: string;
  turns: SessionTurn[];
}

/** 通用错误响应 */
export interface ErrorResponse {
  error: string;
  detail?: string | null;
}

/** 用户记忆响应（GET /api/v1/users/{id}/memory） */
export interface UserMemoryResponse {
  user_id: string;
  term_preferences: Record<string, unknown>;
  frequently_used_tables: Record<string, unknown>;
  metric_definitions: Record<string, unknown>;
  query_preferences: Record<string, unknown>;
  domain_context: Record<string, unknown>;
  clarification_history: Record<string, unknown>[];
}

/** 指标定义（GET /api/v1/users/{id}/metrics 中 metrics 元素） */
export interface MetricDefinition {
  name?: string;
  description?: string;
  sql_pattern?: string;
  source?: string;
  confidence?: number;
  [key: string]: unknown;
}

/** 指标定义列表响应 */
export interface MetricDefinitionResponse {
  user_id: string;
  metrics: MetricDefinition[];
}

/** 健康检查响应（GET /api/v1/health） */
export interface HealthResponse {
  status: string;
  service: string;
  db_pool?: unknown;
}

// ============================================================================
// SSE 事件类型联合（镜像 query.py docstring）
// ============================================================================

/** stage：节点开始/结束 */
export interface StageEvent {
  type: 'stage';
  data: {
    query_id: string;
    /** 节点名，如 ir / cg / execute / decision / run_subqueries 等 */
    node: string;
    /** started | done */
    status: 'started' | 'done';
    /** done 时若节点出错携带 */
    error?: string;
    /** done 时若拒答携带 */
    rejection_reason?: string;
    /** run_subqueries 节点携带子查询列表 */
    subqueries?: string[];
  };
}

/** cache_check：历史命中检测 */
export interface CacheCheckEvent {
  type: 'cache_check';
  data: {
    query_id: string;
    hit: boolean;
    source: string;
    confidence: number;
    cached_sql: string | null;
    recalled: number;
    historical_sql_refs?: unknown[];
  };
}

/** llm_thinking：qwen3 思考链片段（按节点累积） */
export interface LlmThinkingEvent {
  type: 'llm_thinking';
  data: {
    query_id: string;
    /** 当前所在 graph 节点名 */
    node: string;
    /** 思考链文本片段 */
    text: string;
  };
}

/** keywords：IR 关键词提取 */
export interface KeywordsEvent {
  type: 'keywords';
  data: {
    query_id: string;
    groups: { name: string; expansions?: string[] }[];
  };
}

/** schema_recall：IR schema 召回 */
export interface SchemaRecallEvent {
  type: 'schema_recall';
  data: {
    query_id: string;
    groups: { name: string; top_columns: string[] }[];
  };
}

/** answerability：可回答性检查 */
export interface AnswerabilityEvent {
  type: 'answerability';
  data: {
    query_id: string;
    answerable: boolean;
    confidence: number | null;
    reason: string;
  };
}

/** sql_candidates：CG 候选 SQL */
export interface SqlCandidatesEvent {
  type: 'sql_candidates';
  data: {
    query_id: string;
    candidates: { id: string; sql: string }[];
  };
}

/** execution：单条候选 SQL 执行结果 */
export interface ExecutionEvent {
  type: 'execution';
  data: {
    query_id: string;
    candidate_id: string | null;
    success: boolean;
    rows: number | null;
    error: string | null;
  };
}

/** final_decision：最终决策 */
export interface FinalDecisionEvent {
  type: 'final_decision';
  data: {
    query_id: string;
    selected_id?: string | null;
    selected_sql?: string | null;
    decision_path?: string;
    fix_failed?: boolean;
    reason?: string;
    /** 多意图聚合场景携带 */
    multi_intent?: boolean;
    subquery_count?: number;
    success_count?: number;
  };
}

/** clarification：反问（图 interrupt 挂起） */
export interface ClarificationEvent {
  type: 'clarification';
  data: {
    query_id: string;
    question: string;
    ambiguities: string[];
    round: number;
    awaiting_answer: boolean;
  };
}

/** result：最终结果 */
export interface ResultEvent {
  type: 'result';
  data: {
    query_id: string;
    sql: string;
    result: Record<string, unknown>[];
  };
}

/** error：错误（rejection=true 为拒答） */
export interface ErrorEvent {
  type: 'error';
  data: {
    query_id: string;
    error: string;
    node?: string;
    rejection?: boolean;
  };
}

/** done：整条 query 完成 */
export interface DoneEvent {
  type: 'done';
  data: {
    query_id: string;
    has_result: boolean;
    awaiting_clarification: boolean;
    fix_failed: boolean;
    decision_path: string;
    fix_rounds_used: number;
    last_error: string | null;
  };
}

/** 全部 SSE 事件联合类型 */
export type SseEvent =
  | StageEvent
  | CacheCheckEvent
  | LlmThinkingEvent
  | KeywordsEvent
  | SchemaRecallEvent
  | AnswerabilityEvent
  | SqlCandidatesEvent
  | ExecutionEvent
  | FinalDecisionEvent
  | ClarificationEvent
  | ResultEvent
  | ErrorEvent
  | DoneEvent;

/** SSE 事件类型字面量联合 */
export type SseEventType = SseEvent['type'];

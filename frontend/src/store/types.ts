/**
 * 状态层类型定义（决策 D3）
 *
 * Turn = 一个用户问题及其全部后续（含反问 resume）。
 * 客户端 turnId 作主键，跨 resume 稳定（D4）；server query_id 仅日志用。
 */
import type { SseEvent } from '@/api/types';

/** 时间轴节点状态 */
export type NodeStatus = 'pending' | 'active' | 'done' | 'error';

/**
 * 时间轴节点类型（对应推理阶段）。
 * - cache        历史缓存命中
 * - ir           信息检索（关键词 + schema 召回）
 * - answerability 可回答性检查
 * - cg           SQL 候选生成
 * - execution    SQL 执行
 * - decision     最终决策
 * - clarify      反问
 * - result       最终结果
 * - error        拒答/错误
 */
export type TimelineNodeType =
  | 'cache'
  | 'ir'
  | 'ss'
  | 'answerability'
  | 'cg'
  | 'execution'
  | 'decision'
  | 'clarify'
  | 'result'
  | 'error';

/** 时间轴节点（常驻时间轴的一行） */
export interface TimelineNode {
  type: TimelineNodeType;
  status: NodeStatus;
  /** 一行摘要（命中/关键词/候选数/可回答性/决策） */
  summary: string;
}

/** Turn 状态机 */
export type TurnStatus =
  | 'streaming'
  | 'done'
  | 'error'
  | 'awaiting_clarification';

/** 单条候选 SQL 执行结果 */
export interface ExecutionResult {
  candidateId: string | null;
  success: boolean;
  rows: number | null;
  error: string | null;
}

/** IR 关键词组召回详情（D3/D5：按组聚合的字段与值召回） */
export interface KeywordGroupDetail {
  phrase: string;
  terms: string[];
  columns: { table: string; column: string; score: number }[];
  values: { value: string; table: string; column: string; score: number }[];
}

/** Turn 详情：按节点类型存结构化产物（供检查器渲染） */
export interface TurnDetails {
  cache?: {
    hit: boolean;
    source: string;
    confidence: number;
    cachedSql: string | null;
    matchedMetricName?: string | null;
    historicalQuery?: string | null;
  };
  ir?: { keywordGroups: KeywordGroupDetail[] };
  schemaFinalize?: { joinEdges: number; bridgeTables: number };
  answerability?: {
    answerable: boolean;
    confidence: number | null;
    reason: string;
  };
  candidates?: { id: string; sql: string }[];
  /** by candidate_id */
  exec?: Record<string, ExecutionResult>;
  decision?: {
    selectedId?: string | null;
    selectedSql?: string | null;
    decisionPath?: string;
    fixFailed?: boolean;
    reason?: string;
    multiIntent?: boolean;
    subqueryCount?: number;
    successCount?: number;
  };
}

/** 反问上下文 */
export interface Clarification {
  question: string;
  ambiguities: string[];
  round: number;
}

/** done 事件携带的决策路径/修复信息 */
export interface DoneMeta {
  hasResult: boolean;
  awaitingClarification: boolean;
  fixFailed: boolean;
  decisionPath: string;
  fixRoundsUsed: number;
  lastError: string | null;
}

/** 一个用户问题及其全部后续（含反问 resume） */
export interface Turn {
  /** 客户端生成，跨 resume 稳定（D4） */
  turnId: string;
  /** 服务端 query_id，仅日志用，不参与主键 */
  queryId?: string;
  userQuery: string;
  /** 有序时间轴节点 */
  timeline: TimelineNode[];
  /** 按节点类型存结构化产物 */
  details: TurnDetails;
  /** qwen3 思考链按节点累积 */
  thinking: Record<string, string>;
  /** 最终结果 */
  result: { sql: string; rows: Record<string, unknown>[] } | null;
  /** 状态机 */
  status: TurnStatus;
  /** 检查器当前显示节点，null=自动跟随最新（D5） */
  selectedNode: TimelineNodeType | null;
  /** 反问上下文 */
  clarification: Clarification | null;
  /** done 事件元信息 */
  doneMeta?: DoneMeta;
  /** 拒答标记 */
  rejection?: boolean;
  /** 错误信息 */
  error?: string;
}

/** 用于 reduceSseEvent 的 error 降级事件（网络错误转成的事件） */
export type SyntheticErrorEvent = Extract<SseEvent, { type: 'error' }>;

/**
 * SSE 事件 reducer（决策 D3/D4）
 *
 * `reduceSseEvent(turn, event) -> Turn` 纯函数：处理全部 SSE 事件类型，
 * 把事件累积进对应 Turn 的时间轴 / 详情 / 思考链 / 结果。
 *
 * 关键规则：
 * - clarification 事件 -> Turn.status='awaiting_clarification'
 * - error 事件（rejection=true）-> 拒答标记 + status='error'，不期待 result
 * - done 事件 -> 据 awaiting_clarification / 是否已有 error 决定终态
 * - cache_check hit=true -> 缓存短路，仅点亮 cache 节点（跳过 ir/ss/cg/execution）
 * - llm_thinking -> 按 node 累积 text，不进时间轴
 * - turnId 与 server query_id 解耦：query_id 仅记录，不改主键
 */
import type { SseEvent } from '@/api/types';
import type {
  TimelineNode,
  TimelineNodeType,
  Turn,
} from './types';

/** 创建空 Turn（status=streaming，selectedNode=null 自动跟随） */
export function createTurn(turnId: string, userQuery: string): Turn {
  return {
    turnId,
    userQuery,
    timeline: [],
    details: {},
    thinking: {},
    result: null,
    status: 'streaming',
    selectedNode: null,
    clarification: null,
  };
}

/**
 * upsert 时间轴节点：同类型节点存在则合并 patch，否则追加。
 * 默认 status=done（业务事件到达即视为该节点完成）。
 */
function upsert(
  timeline: TimelineNode[],
  type: TimelineNodeType,
  patch: Partial<TimelineNode>,
): TimelineNode[] {
  const idx = timeline.findIndex((n) => n.type === type);
  if (idx === -1) {
    return [...timeline, { type, status: 'done', summary: '', ...patch }];
  }
  const copy = timeline.slice();
  copy[idx] = { ...copy[idx], ...patch };
  return copy;
}

/**
 * stage 事件 node 名 -> 时间轴节点类型映射。
 *
 * 后端节点名来自 graph 注册（ir / cg / execute_all / decision / task_planner
 * / run_subqueries 等）。未匹配的 stage 不单独点亮新节点，但不报错。
 */
function mapStageNode(node: string): TimelineNodeType | null {
  const n = (node || '').toLowerCase();
  if (n === 'ir' || n.includes('retrieval') || n.includes('preprocess') || n.includes('task_plan')) {
    return 'ir';
  }
  if (n === 'cg' || n.includes('generation') || n.includes('generate')) {
    return 'cg';
  }
  if (n.includes('execute')) {
    return 'execution';
  }
  if (n.includes('decision') || n.includes('decide')) {
    return 'decision';
  }
  if (n.includes('answerab')) {
    return 'answerability';
  }
  return null;
}

/** 数值格式化为两位置信度 */
function fmtConf(c: number | null | undefined): string {
  if (c == null || Number.isNaN(c)) return '-';
  return (typeof c === 'number' ? c.toFixed(2) : String(c));
}

/**
 * 把一个 SSE 事件 reduce 进 Turn，返回新 Turn（不可变）。
 */
export function reduceSseEvent(turn: Turn, event: SseEvent): Turn {
  // 记录 server query_id（仅日志，不改主键）
  const next: Turn = { ...turn, queryId: event.data.query_id ?? turn.queryId };
  let timeline = next.timeline;
  let details = next.details;
  let thinking = next.thinking;

  switch (event.type) {
    case 'stage': {
      const tlType = mapStageNode(event.data.node);
      if (tlType) {
        const status = event.data.status === 'started' ? 'active' : 'done';
        const existing = timeline.find((n) => n.type === tlType);
        if (existing) {
          timeline = upsert(timeline, tlType, { status });
        } else {
          timeline = [...timeline, { type: tlType, status, summary: '' }];
        }
      }
      // stage done 携带 rejection_reason / error（节点内拒答/失败）
      if (event.data.rejection_reason) {
        next.rejection = true;
        next.error = event.data.rejection_reason;
        timeline = upsert(timeline, 'error', {
          status: 'done',
          summary: `拒答: ${event.data.rejection_reason}`,
        });
        next.status = 'error';
      } else if (event.data.error) {
        next.error = event.data.error;
      }
      break;
    }

    case 'cache_check': {
      const d = event.data;
      details = {
        ...details,
        cache: {
          hit: d.hit,
          source: d.source,
          confidence: d.confidence,
          cachedSql: d.cached_sql,
        },
      };
      if (d.hit) {
        // 缓存命中短路：仅点亮 cache 节点
        timeline = upsert(timeline, 'cache', {
          status: 'done',
          summary: `缓存命中 · ${d.source} · conf=${fmtConf(d.confidence)}`,
        });
      }
      break;
    }

    case 'llm_thinking': {
      // 按节点累积思考链文本，不进时间轴
      const d = event.data;
      thinking = { ...thinking, [d.node]: (thinking[d.node] ?? '') + d.text };
      break;
    }

    case 'keywords': {
      details = { ...details, keywords: event.data.groups };
      timeline = upsert(timeline, 'ir', {
        status: 'done',
        summary: `关键词 ${event.data.groups.length} 组`,
      });
      break;
    }

    case 'schema_recall': {
      details = { ...details, schemaRecall: event.data.groups };
      const prev = timeline.find((n) => n.type === 'ir');
      timeline = upsert(timeline, 'ir', {
        status: 'done',
        summary: prev?.summary
          ? `${prev.summary} · 召回 ${event.data.groups.length} 组`
          : `召回 ${event.data.groups.length} 组 schema`,
      });
      break;
    }

    case 'answerability': {
      const d = event.data;
      details = {
        ...details,
        answerability: {
          answerable: d.answerable,
          confidence: d.confidence,
          reason: d.reason,
        },
      };
      timeline = upsert(timeline, 'answerability', {
        status: 'done',
        summary: d.answerable
          ? `可回答 · conf=${fmtConf(d.confidence)}`
          : `不可回答: ${d.reason}`,
      });
      break;
    }

    case 'sql_candidates': {
      details = { ...details, candidates: event.data.candidates };
      timeline = upsert(timeline, 'cg', {
        status: 'done',
        summary: `${event.data.candidates.length} 候选 SQL`,
      });
      break;
    }

    case 'execution': {
      const d = event.data;
      const id = d.candidate_id ?? 'unknown';
      const exec = {
        ...(details.exec ?? {}),
        [id]: {
          candidateId: d.candidate_id,
          success: d.success,
          rows: d.rows,
          error: d.error,
        },
      };
      details = { ...details, exec };
      const total = Object.keys(exec).length;
      const ok = Object.values(exec).filter((e) => e.success).length;
      timeline = upsert(timeline, 'execution', {
        status: 'done',
        summary: `执行 ${total} 候选 · ${ok} 成功`,
      });
      break;
    }

    case 'final_decision': {
      const d = event.data;
      details = {
        ...details,
        decision: {
          selectedId: d.selected_id,
          selectedSql: d.selected_sql,
          decisionPath: d.decision_path,
          fixFailed: d.fix_failed,
          reason: d.reason,
          multiIntent: d.multi_intent,
          subqueryCount: d.subquery_count,
          successCount: d.success_count,
        },
      };
      timeline = upsert(timeline, 'decision', {
        status: 'done',
        summary: d.multi_intent
          ? `多意图决策 · ${d.success_count ?? 0}/${d.subquery_count ?? 0} 成功`
          : d.selected_id
            ? `选中 ${d.selected_id}`
            : '决策完成',
      });
      break;
    }

    case 'clarification': {
      const d = event.data;
      next.clarification = {
        question: d.question,
        ambiguities: d.ambiguities,
        round: d.round,
      };
      next.status = 'awaiting_clarification';
      timeline = upsert(timeline, 'clarify', {
        status: 'done',
        summary: `反问第 ${d.round} 轮: ${d.question}`,
      });
      break;
    }

    case 'result': {
      const d = event.data;
      next.result = { sql: d.sql, rows: d.result ?? [] };
      timeline = upsert(timeline, 'result', {
        status: 'done',
        summary: `${d.result?.length ?? 0} 行结果`,
      });
      break;
    }

    case 'error': {
      const d = event.data;
      next.error = d.error;
      if (d.rejection) next.rejection = true;
      next.status = 'error';
      timeline = upsert(timeline, 'error', {
        status: 'done',
        summary: d.rejection ? `拒答: ${d.error}` : `错误: ${d.error}`,
      });
      break;
    }

    case 'done': {
      const d = event.data;
      next.doneMeta = {
        hasResult: d.has_result,
        awaitingClarification: d.awaiting_clarification,
        fixFailed: d.fix_failed,
        decisionPath: d.decision_path,
        fixRoundsUsed: d.fix_rounds_used,
        lastError: d.last_error,
      };
      if (d.awaiting_clarification) {
        next.status = 'awaiting_clarification';
      } else if (next.status !== 'error') {
        next.status = 'done';
      }
      break;
    }

    default: {
      // 未知事件类型，忽略
    }
  }

  next.timeline = timeline;
  next.details = details;
  next.thinking = thinking;
  return next;
}

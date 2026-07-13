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
  // 有 id 按 id 匹配（多轮节点 rewrite_detect/rewrite），无 id 按 type 匹配（单次节点）
  const id = patch.id;
  const idx = id !== undefined
    ? timeline.findIndex((n) => n.id === id)
    : timeline.findIndex((n) => n.type === type);
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
  if (n === 'pre_reject') {
    return 'pre_reject';
  }
  if (n === 'ir' || n.includes('retrieval') || n.includes('preprocess') || n.includes('task_plan')) {
    return 'ir';
  }
  if (n === 'ss' || n.includes('schema_select') || n === 'schema_finalize' || n.includes('schema_final')) {
    return 'ss';
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
  // cancelled turn 不再处理后续事件（change clarify-choice-inspector-cancel）：
  // 避免 abort 后已 buffer 的事件继续往 timeline 追加节点（"点停止后又接着跑"）
  if (turn.status === 'cancelled') {
    return turn;
  }
  // 记录 server query_id（仅日志，不改主键）
  const next: Turn = { ...turn, queryId: event.data.query_id ?? turn.queryId };
  let timeline = next.timeline;
  let details = next.details;
  let thinking = next.thinking;

  switch (event.type) {
    case 'stage': {
      const d = event.data;
      const tlType = mapStageNode(d.node);
      if (tlType === 'pre_reject') {
        // 前置拒答节点：通过->"通过"，拒答->"拒答: 原因"（置 error 态，不落通用 error 节点）
        const isReject = !!d.rejection_reason;
        const status = isReject ? 'error' : (d.status === 'started' ? 'active' : 'done');
        timeline = upsert(timeline, 'pre_reject', {
          status,
          summary: isReject ? `拒答: ${d.rejection_reason}` : (d.status === 'done' ? '通过' : ''),
        });
        if (isReject) {
          next.rejection = true;
          next.error = d.rejection_reason;
          next.status = 'error';
          details = { ...details, preReject: { passed: false, reason: d.rejection_reason, category: d.category } };
        } else if (d.status === 'done') {
          details = { ...details, preReject: { passed: true, category: d.category } };
        }
      } else if (tlType) {
        const status = d.status === 'started' ? 'active' : 'done';
        const existing = timeline.find((n) => n.type === tlType);
        if (existing) {
          timeline = upsert(timeline, tlType, { status });
        } else {
          timeline = [...timeline, { type: tlType, status, summary: '' }];
        }
      }
      // 非 pre_reject 节点的 stage done 拒答/失败（pre_reject / schema_empty 已自行处理）
      if (d.rejection_reason && tlType !== 'pre_reject') {
        const hasSchemaEmptyError = timeline.some(
          (n) => n.type === 'schema_empty' && n.status === 'error',
        );
        next.rejection = true;
        next.error = d.rejection_reason;
        if (!hasSchemaEmptyError) {
          timeline = upsert(timeline, 'error', {
            status: 'done',
            summary: `拒答: ${d.rejection_reason}`,
          });
        }
        next.status = 'error';
      } else if (d.error) {
        next.error = d.error;
      }
      break;
    }

    case 'rewrite_detect': {
      // 改写问题检测（每轮独立节点，id=detect_r{round}）
      const d = event.data;
      const id = `detect_r${d.round}`;
      const summary = d.has_issues
        ? `检测到 ${d.issue_types.join('·') || '问题'}`
        : '无问题';
      timeline = upsert(timeline, 'rewrite_detect', { id, status: 'done', summary });
      const rounds = [...(details.rewriteDetect?.rounds ?? [])];
      const entry = {
        round: d.round,
        hasIssues: d.has_issues,
        issueDetail: d.issue_detail,
        issueTypes: [...d.issue_types],
      };
      const ridx = rounds.findIndex((r) => r.round === d.round);
      if (ridx >= 0) rounds[ridx] = entry; else rounds.push(entry);
      details = { ...details, rewriteDetect: { rounds } };
      break;
    }

    case 'rewrite': {
      // 改写执行（每轮独立节点，id=rewrite_r{round}）
      const d = event.data;
      const id = `rewrite_r${d.rewrite_round}`;
      timeline = upsert(timeline, 'rewrite', {
        id,
        status: 'done',
        summary: `改写第 ${d.rewrite_round} 轮`,
      });
      const rounds = [...(details.rewrite?.rounds ?? [])];
      const entry = {
        round: d.rewrite_round,
        originalQuery: d.original_query,
        rewrittenQuery: d.rewritten_query,
        reason: d.rewrite_reason,
      };
      const ridx = rounds.findIndex((r) => r.round === d.rewrite_round);
      if (ridx >= 0) rounds[ridx] = entry; else rounds.push(entry);
      details = { ...details, rewrite: { rounds } };
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
          matchedMetricName: d.matched_metric_name ?? null,
          historicalQuery: d.historical_query ?? null,
        },
      };
      if (d.hit) {
        // 缓存命中短路：仅点亮 cache 节点
        // 摘要按 source 区分：长期记忆显示指标名、会话历史显示历史 query
        let label: string;
        if (d.source === 'metric_definition') {
          label = d.matched_metric_name ? `长期记忆·${d.matched_metric_name}` : '长期记忆';
        } else if (d.source === 'session_history') {
          const q = d.historical_query ?? '';
          label = q ? `会话历史·${q.length > 12 ? q.slice(0, 12) + '…' : q}` : '会话历史';
        } else {
          label = '缓存命中';
        }
        timeline = upsert(timeline, 'cache', {
          status: 'done',
          summary: `${label} · conf=${fmtConf(d.confidence)}`,
        });
      }
      break;
    }

    case 'value_rewrite': {
      // 值参数改写（cache 命中后比对历史查询改写值参数）
      const d = event.data;
      details = {
        ...details,
        valueRewrite: {
          historicalQuery: d.historical_query,
          userQuery: d.user_query,
          cachedSql: d.cached_sql,
          adjustedCachedSql: d.adjusted_cached_sql,
          changed: d.changed,
          reason: d.reason,
        },
      };
      timeline = upsert(timeline, 'value_rewrite', {
        status: 'done',
        summary: d.changed ? '✓' : '未变更',
      });
      break;
    }

    case 'cache_confirm': {
      // 复用确认（用户对 cache 反问的回答结果）
      const d = event.data;
      details = {
        ...details,
        cacheConfirm: {
          approved: d.approved,
          userChoice: d.user_choice,
          historicalQuery: d.historical_query,
          userQuery: d.user_query,
        },
      };
      timeline = upsert(timeline, 'cache_confirm', {
        status: 'done',
        summary: d.approved ? '✓' : '✗',
      });
      break;
    }

    case 'llm_thinking': {
      // 按节点累积思考链文本，不进时间轴
      const d = event.data;
      thinking = { ...thinking, [d.node]: (thinking[d.node] ?? '') + d.text };
      break;
    }

    case 'keywords': {
      const groups = event.data.groups;
      details = {
        ...details,
        ir: {
          keywordGroups: groups.map((g) => ({
            phrase: g.name,
            terms: g.expansions ?? [],
            columns: [],
            values: [],
          })),
        },
      };
      timeline = upsert(timeline, 'ir', {
        status: 'done',
        summary: `关键词 ${groups.length} 组`,
      });
      break;
    }

    case 'schema_recall': {
      const kwGroups = event.data.keyword_groups;
      details = { ...details, ir: { keywordGroups: kwGroups } };
      const prev = timeline.find((n) => n.type === 'ir');
      const colTotal = kwGroups.reduce((s, g) => s + g.columns.length, 0);
      const valTotal = kwGroups.reduce((s, g) => s + g.values.length, 0);
      timeline = upsert(timeline, 'ir', {
        status: 'done',
        summary: prev?.summary
          ? `${prev.summary} · 召回 ${kwGroups.length} 组（${colTotal} 字段/${valTotal} 值）`
          : `召回 ${kwGroups.length} 组（${colTotal} 字段/${valTotal} 值）`,
      });
      break;
    }

    case 'schema_finalize': {
      const d = event.data;
      details = {
        ...details,
        schemaFinalize: {
          joinEdges: d.join_edges,
          bridgeTables: d.bridge_tables,
        },
      };
      const prevSs = timeline.find((n) => n.type === 'ss');
      timeline = upsert(timeline, 'ss', {
        status: 'done',
        summary: prevSs?.summary
          ? `${prevSs.summary} · JOIN 边 ${d.join_edges} · 桥接表 ${d.bridge_tables}`
          : `JOIN 边 ${d.join_edges} · 桥接表 ${d.bridge_tables}`,
      });
      break;
    }

    case 'schema_empty': {
      // SS 未选出任何表时显式拒答（D10）：独立 error 节点，不再静默中断
      const d = event.data;
      details = { ...details, schemaEmpty: { reason: d.reason } };
      timeline = upsert(timeline, 'schema_empty', {
        status: 'error',
        summary: '未匹配相关表',
      });
      next.rejection = true;
      next.error = d.reason;
      next.status = 'error';
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
        kind: d.kind ?? null,
        options: d.options ?? [],
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
      // 前置拒答 / schema 空拒答已由对应节点独占呈现，不再重复 upsert 通用 error 节点
      const hasPreRejectError = timeline.some(
        (n) => n.type === 'pre_reject' && n.status === 'error',
      );
      const hasSchemaEmptyError = timeline.some(
        (n) => n.type === 'schema_empty' && n.status === 'error',
      );
      if (!hasPreRejectError && !hasSchemaEmptyError) {
        timeline = upsert(timeline, 'error', {
          status: 'done',
          summary: d.rejection ? `拒答: ${d.error}` : `错误: ${d.error}`,
        });
      }
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
      } else if (next.status !== 'error' && next.status !== 'cancelled') {
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

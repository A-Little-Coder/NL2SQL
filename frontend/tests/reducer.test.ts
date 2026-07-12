/**
 * reducer 单测（任务 14.2）
 *
 * 覆盖 reduceSseEvent 各事件类型 -> Turn 状态：
 * cache 命中短路、keywords/schema_recall、clarification 设 awaiting、
 * rejection 标记、非 rejection error、done 收尾、llm_thinking 累积、
 * query_id 记录但不影响 turnId。
 */
import { createTurn, reduceSseEvent } from '../src/store/reducer';
import type { SseEvent } from '../src/api/types';

/** 构造任意 SSE 事件（测试用，绕过字面量类型校验） */
function ev(e: { type: string; data: Record<string, unknown> }): SseEvent {
  return e as unknown as SseEvent;
}

describe('reduceSseEvent', () => {
  test('createTurn 初始状态', () => {
    const t = createTurn('t1', '查询');
    expect(t.status).toBe('streaming');
    expect(t.selectedNode).toBeNull();
    expect(t.timeline).toEqual([]);
    expect(t.result).toBeNull();
    expect(t.clarification).toBeNull();
  });

  test('cache 命中 metric_definition 带指标名：摘要显示"长期记忆·{name}"', () => {
    let t = createTurn('t1', 'q');
    t = reduceSseEvent(
      t,
      ev({
        type: 'cache_check',
        data: { query_id: 'q1', hit: true, source: 'metric_definition', confidence: 0.92, cached_sql: 'SELECT SUM(amount) FROM sales', recalled: 0, matched_metric_name: '销售额', historical_query: null },
      }),
    );
    expect(t.details.cache?.hit).toBe(true);
    expect(t.details.cache?.matchedMetricName).toBe('销售额');
    const node = t.timeline.find((n) => n.type === 'cache');
    expect(node).toBeTruthy();
    expect(node?.summary).toBe('长期记忆·销售额 · conf=0.92');
  });

  test('cache 命中 metric_definition 无指标名：摘要显示"长期记忆"', () => {
    let t = createTurn('t1', 'q');
    t = reduceSseEvent(
      t,
      ev({
        type: 'cache_check',
        data: { query_id: 'q1', hit: true, source: 'metric_definition', confidence: 0.92, cached_sql: 'SELECT 1', recalled: 0, matched_metric_name: null, historical_query: null },
      }),
    );
    const node = t.timeline.find((n) => n.type === 'cache');
    expect(node?.summary).toBe('长期记忆 · conf=0.92');
  });

  test('cache 命中 session_history 带历史 query：摘要显示"会话历史·{query}"', () => {
    let t = createTurn('t1', 'q');
    t = reduceSseEvent(
      t,
      ev({
        type: 'cache_check',
        data: { query_id: 'q1', hit: true, source: 'session_history', confidence: 0.95, cached_sql: 'SELECT 1', recalled: 1, matched_metric_name: null, historical_query: '查询苹果的销售额' },
      }),
    );
    expect(t.details.cache?.historicalQuery).toBe('查询苹果的销售额');
    const node = t.timeline.find((n) => n.type === 'cache');
    expect(node?.summary).toBe('会话历史·查询苹果的销售额 · conf=0.95');
  });

  test('cache 命中 session_history 超 12 字截断', () => {
    let t = createTurn('t1', 'q');
    const longQuery = '查询华东区今年各门店的销售额排名情况';
    t = reduceSseEvent(
      t,
      ev({
        type: 'cache_check',
        data: { query_id: 'q1', hit: true, source: 'session_history', confidence: 0.9, cached_sql: 'SELECT 1', recalled: 1, matched_metric_name: null, historical_query: longQuery },
      }),
    );
    const node = t.timeline.find((n) => n.type === 'cache');
    expect(node?.summary).toBe(`会话历史·${longQuery.slice(0, 12)}… · conf=0.90`);
  });

  test('cache 命中未知 source：摘要回退"缓存命中"', () => {
    let t = createTurn('t1', 'q');
    t = reduceSseEvent(
      t,
      ev({
        type: 'cache_check',
        data: { query_id: 'q1', hit: true, source: 'history', confidence: 0.9, cached_sql: 'SELECT 1', recalled: 0 },
      }),
    );
    const node = t.timeline.find((n) => n.type === 'cache');
    expect(node?.summary).toBe('缓存命中 · conf=0.90');
  });

  test('cache 未命中不点亮 cache 节点', () => {
    let t = createTurn('t1', 'q');
    t = reduceSseEvent(
      t,
      ev({ type: 'cache_check', data: { query_id: 'q1', hit: false, source: '', confidence: 0, cached_sql: null, recalled: 3 } }),
    );
    expect(t.timeline.find((n) => n.type === 'cache')).toBeFalsy();
  });

  test('keywords + schema_recall -> ir 节点 done + ir.keywordGroups 填充', () => {
    let t = createTurn('t1', 'q');
    t = reduceSseEvent(t, ev({ type: 'keywords', data: { query_id: 'q1', groups: [{ name: 'k', expansions: ['e'] }] } }));
    // keywords 事件先填 phrase/terms（columns/values 空）
    expect(t.details.ir?.keywordGroups.length).toBe(1);
    expect(t.details.ir?.keywordGroups[0].phrase).toBe('k');
    expect(t.details.ir?.keywordGroups[0].terms).toEqual(['e']);
    expect(t.details.ir?.keywordGroups[0].columns).toEqual([]);
    t = reduceSseEvent(t, ev({
      type: 'schema_recall',
      data: {
        query_id: 'q1',
        keyword_groups: [{
          phrase: 'g',
          terms: ['g'],
          columns: [{ table: 't', column: 'c', score: 0.9 }],
          values: [{ value: 'v', table: 't', column: 'c', score: 0.8 }],
        }],
      },
    }));
    // schema_recall 覆盖为完整结构
    expect(t.details.ir?.keywordGroups[0].columns.length).toBe(1);
    expect(t.details.ir?.keywordGroups[0].values.length).toBe(1);
    expect(t.timeline.find((n) => n.type === 'ir')?.status).toBe('done');
  });

  test('ss stage -> ss 时间轴节点', () => {
    let t = createTurn('t1', 'q');
    t = reduceSseEvent(t, ev({ type: 'stage', data: { query_id: 'q1', node: 'ss', status: 'started' } }));
    expect(t.timeline.find((n) => n.type === 'ss')?.status).toBe('active');
    t = reduceSseEvent(t, ev({ type: 'stage', data: { query_id: 'q1', node: 'ss', status: 'done' } }));
    expect(t.timeline.find((n) => n.type === 'ss')?.status).toBe('done');
  });

  test('schema_finalize stage 节点名映射到 ss', () => {
    let t = createTurn('t1', 'q');
    t = reduceSseEvent(t, ev({ type: 'stage', data: { query_id: 'q1', node: 'schema_finalize', status: 'started' } }));
    expect(t.timeline.find((n) => n.type === 'ss')).toBeTruthy();
  });

  test('schema_finalize 事件填充 details.schemaFinalize + 更新 ss 摘要', () => {
    let t = createTurn('t1', 'q');
    t = reduceSseEvent(t, ev({ type: 'stage', data: { query_id: 'q1', node: 'ss', status: 'done' } }));
    t = reduceSseEvent(t, ev({ type: 'schema_finalize', data: { join_edges: 2, bridge_tables: 1 } }));
    expect(t.details.schemaFinalize?.joinEdges).toBe(2);
    expect(t.details.schemaFinalize?.bridgeTables).toBe(1);
    const ssNode = t.timeline.find((n) => n.type === 'ss');
    expect(ssNode?.status).toBe('done');
    expect(ssNode?.summary).toContain('JOIN 边 2');
    expect(ssNode?.summary).toContain('桥接表 1');
  });

  test('answerability 不可回答时摘要含原因', () => {
    let t = createTurn('t1', 'q');
    t = reduceSseEvent(
      t,
      ev({ type: 'answerability', data: { query_id: 'q1', answerable: false, confidence: 0.2, reason: '缺维度' } }),
    );
    expect(t.details.answerability?.answerable).toBe(false);
    expect(t.timeline.find((n) => n.type === 'answerability')?.summary).toContain('缺维度');
  });

  test('sql_candidates + execution 填充 details', () => {
    let t = createTurn('t1', 'q');
    t = reduceSseEvent(t, ev({ type: 'sql_candidates', data: { query_id: 'q1', candidates: [{ id: 'c1', sql: 'SELECT 1' }] } }));
    t = reduceSseEvent(t, ev({ type: 'execution', data: { query_id: 'q1', candidate_id: 'c1', success: true, rows: 5, error: null } }));
    expect(t.details.candidates?.length).toBe(1);
    expect(t.details.exec?.['c1'].rows).toBe(5);
    expect(t.timeline.find((n) => n.type === 'execution')).toBeTruthy();
  });

  test('final_decision 填充 decision', () => {
    let t = createTurn('t1', 'q');
    t = reduceSseEvent(
      t,
      ev({ type: 'final_decision', data: { query_id: 'q1', selected_id: 'c1', selected_sql: 'SELECT 1', decision_path: 'direct', fix_failed: false, reason: 'ok' } }),
    );
    expect(t.details.decision?.selectedId).toBe('c1');
    expect(t.timeline.find((n) => n.type === 'decision')).toBeTruthy();
  });

  test('clarification 设 awaiting_clarification + clarification 上下文', () => {
    let t = createTurn('t1', 'q');
    t = reduceSseEvent(
      t,
      ev({ type: 'clarification', data: { query_id: 'q1', question: '哪个季度?', ambiguities: ['Q1', 'Q2'], round: 1, awaiting_answer: true } }),
    );
    expect(t.status).toBe('awaiting_clarification');
    expect(t.clarification?.round).toBe(1);
    expect(t.clarification?.ambiguities).toEqual(['Q1', 'Q2']);
    expect(t.timeline.find((n) => n.type === 'clarify')).toBeTruthy();
  });

  test('error rejection=true 标记拒答 + status=error', () => {
    let t = createTurn('t1', 'q');
    t = reduceSseEvent(t, ev({ type: 'error', data: { query_id: 'q1', error: '不可回答', rejection: true } }));
    expect(t.rejection).toBe(true);
    expect(t.status).toBe('error');
    expect(t.error).toBe('不可回答');
  });

  test('error 非 rejection -> status=error，无 rejection 标记', () => {
    let t = createTurn('t1', 'q');
    t = reduceSseEvent(t, ev({ type: 'error', data: { query_id: 'q1', error: 'boom' } }));
    expect(t.status).toBe('error');
    expect(t.rejection).toBeUndefined();
  });

  test('result + done -> status=done，result 填充', () => {
    let t = createTurn('t1', 'q');
    t = reduceSseEvent(t, ev({ type: 'result', data: { query_id: 'q1', sql: 'SELECT 1', result: [{ a: 1 }] } }));
    t = reduceSseEvent(
      t,
      ev({ type: 'done', data: { query_id: 'q1', has_result: true, awaiting_clarification: false, fix_failed: false, decision_path: 'direct', fix_rounds_used: 0, last_error: null } }),
    );
    expect(t.status).toBe('done');
    expect(t.result?.rows.length).toBe(1);
    expect(t.doneMeta?.hasResult).toBe(true);
  });

  test('done awaiting_clarification -> status=awaiting_clarification', () => {
    let t = createTurn('t1', 'q');
    t = reduceSseEvent(t, ev({ type: 'clarification', data: { query_id: 'q1', question: 'q?', ambiguities: [], round: 1, awaiting_answer: true } }));
    t = reduceSseEvent(
      t,
      ev({ type: 'done', data: { query_id: 'q1', has_result: false, awaiting_clarification: true, fix_failed: false, decision_path: '', fix_rounds_used: 0, last_error: null } }),
    );
    expect(t.status).toBe('awaiting_clarification');
  });

  test('llm_thinking 按节点累积文本', () => {
    let t = createTurn('t1', 'q');
    t = reduceSseEvent(t, ev({ type: 'llm_thinking', data: { query_id: 'q1', node: 'ir', text: 'hello ' } }));
    t = reduceSseEvent(t, ev({ type: 'llm_thinking', data: { query_id: 'q1', node: 'ir', text: 'world' } }));
    expect(t.thinking['ir']).toBe('hello world');
  });

  test('query_id 记录但不影响 turnId（D4）', () => {
    let t = createTurn('t1', 'q');
    t = reduceSseEvent(t, ev({ type: 'stage', data: { query_id: 'server-qid', node: 'ir', status: 'started' } }));
    expect(t.turnId).toBe('t1');
    expect(t.queryId).toBe('server-qid');
  });

  // ===== 改写/拒答展示（rewrite-refusal-frontend-display）=====

  test('rewrite_detect 事件 -> 检测节点 + details.rewriteDetect', () => {
    let t = createTurn('t1', '那去年的呢');
    t = reduceSseEvent(
      t,
      ev({ type: 'rewrite_detect', data: { query_id: 'q1', round: 1, has_issues: true, issue_detail: '指代缺失', issue_types: ['指代缺失'] } }),
    );
    const node = t.timeline.find((n) => n.type === 'rewrite_detect');
    expect(node).toBeTruthy();
    expect(node?.id).toBe('detect_r1');
    expect(node?.summary).toContain('指代缺失');
    expect(t.details.rewriteDetect?.rounds.length).toBe(1);
    expect(t.details.rewriteDetect?.rounds[0].hasIssues).toBe(true);
  });

  test('rewrite 事件多轮 -> 多个独立 rewrite 节点（id 递增）+ rounds 数组', () => {
    let t = createTurn('t1', 'q');
    t = reduceSseEvent(
      t,
      ev({ type: 'rewrite', data: { query_id: 'q1', original_query: '那去年的呢', rewritten_query: '查苹果去年的销售额', rewrite_reason: '补全指代', rewrite_round: 1 } }),
    );
    t = reduceSseEvent(
      t,
      ev({ type: 'rewrite', data: { query_id: 'q1', original_query: '查苹果去年的销售额', rewritten_query: '查苹果公司2025年的销售额', rewrite_reason: '消歧', rewrite_round: 2 } }),
    );
    const rewriteNodes = t.timeline.filter((n) => n.type === 'rewrite');
    expect(rewriteNodes.length).toBe(2);
    expect(rewriteNodes[0].id).toBe('rewrite_r1');
    expect(rewriteNodes[1].id).toBe('rewrite_r2');
    expect(t.details.rewrite?.rounds.length).toBe(2);
    expect(t.details.rewrite?.rounds[1].rewrittenQuery).toBe('查苹果公司2025年的销售额');
  });

  test('value_rewrite 事件 -> 值改写节点 + details.valueRewrite', () => {
    let t = createTurn('t1', 'q');
    t = reduceSseEvent(
      t,
      ev({
        type: 'value_rewrite',
        data: {
          query_id: 'q1',
          historical_query: '查苹果销售额',
          user_query: '查华为销售额',
          cached_sql: "SELECT * FROM sales WHERE brand='苹果'",
          adjusted_cached_sql: "SELECT * FROM sales WHERE brand='华为'",
          changed: true,
          reason: 'brand 值参数变更',
        },
      }),
    );
    const node = t.timeline.find((n) => n.type === 'value_rewrite');
    expect(node).toBeTruthy();
    expect(node?.summary).toBe('✓');
    expect(t.details.valueRewrite?.changed).toBe(true);
    expect(t.details.valueRewrite?.adjustedCachedSql).toContain('华为');
  });

  test('cache_confirm 事件 -> 确认节点 + details.cacheConfirm', () => {
    let t = createTurn('t1', 'q');
    t = reduceSseEvent(
      t,
      ev({ type: 'cache_confirm', data: { query_id: 'q1', approved: true, user_choice: '复用', historical_query: 'h', user_query: 'q' } }),
    );
    const node = t.timeline.find((n) => n.type === 'cache_confirm');
    expect(node?.summary).toBe('✓');
    expect(t.details.cacheConfirm?.approved).toBe(true);
  });

  test('stage(pre_reject, done, rejection_reason) -> pre_reject error 节点，不生成通用 error', () => {
    let t = createTurn('t1', '删除数据');
    t = reduceSseEvent(t, ev({ type: 'stage', data: { query_id: 'q1', node: 'pre_reject', status: 'started' } }));
    t = reduceSseEvent(t, ev({ type: 'stage', data: { query_id: 'q1', node: 'pre_reject', status: 'done', rejection_reason: '本服务仅支持查询' } }));
    // 模拟 query.py 图结束后发的 error 事件（rejection=true）
    t = reduceSseEvent(t, ev({ type: 'error', data: { query_id: 'q1', error: '本服务仅支持查询', rejection: true } }));
    const preReject = t.timeline.find((n) => n.type === 'pre_reject');
    expect(preReject?.status).toBe('error');
    expect(preReject?.summary).toContain('拒答');
    // 不应出现通用 error 节点
    expect(t.timeline.find((n) => n.type === 'error')).toBeFalsy();
    expect(t.rejection).toBe(true);
    expect(t.details.preReject?.passed).toBe(false);
  });

  test('stage(pre_reject, done) 无 rejection_reason -> pre_reject done 节点 summary="通过"', () => {
    let t = createTurn('t1', '查苹果销售额');
    t = reduceSseEvent(t, ev({ type: 'stage', data: { query_id: 'q1', node: 'pre_reject', status: 'started' } }));
    t = reduceSseEvent(t, ev({ type: 'stage', data: { query_id: 'q1', node: 'pre_reject', status: 'done' } }));
    const preReject = t.timeline.find((n) => n.type === 'pre_reject');
    expect(preReject?.status).toBe('done');
    expect(preReject?.summary).toBe('通过');
    expect(t.details.preReject?.passed).toBe(true);
  });

  test('单次节点（ir/cg）upsert 在 id 改造后仍按 type 合并，无回归', () => {
    let t = createTurn('t1', 'q');
    // ir 节点：stage + keywords + schema_recall 三次事件应合并到同一 ir 节点
    t = reduceSseEvent(t, ev({ type: 'stage', data: { query_id: 'q1', node: 'ir', status: 'started' } }));
    t = reduceSseEvent(t, ev({ type: 'keywords', data: { query_id: 'q1', groups: [{ name: 'k', expansions: ['e'] }] } }));
    t = reduceSseEvent(t, ev({ type: 'schema_recall', data: { query_id: 'q1', keyword_groups: [{ phrase: 'g', terms: ['g'], columns: [], values: [] }] } }));
    expect(t.timeline.filter((n) => n.type === 'ir').length).toBe(1);
    // cg 节点
    t = reduceSseEvent(t, ev({ type: 'sql_candidates', data: { query_id: 'q1', candidates: [{ id: 'c1', sql: 'SELECT 1' }] } }));
    expect(t.timeline.filter((n) => n.type === 'cg').length).toBe(1);
  });

  test('schema_empty 事件 -> schema_empty error 节点 + details.schemaEmpty + 拒答', () => {
    let t = createTurn('t1', 'q');
    t = reduceSseEvent(t, ev({ type: 'schema_empty', data: { query_id: 'q1', reason: '未在数据库中找到与查询相关的表或字段' } }));
    const node = t.timeline.find((n) => n.type === 'schema_empty');
    expect(node).toBeDefined();
    expect(node?.status).toBe('error');
    expect(t.details.schemaEmpty?.reason).toContain('找到');
    expect(t.rejection).toBe(true);
    expect(t.status).toBe('error');
  });

  test('stage(pre_reject, done, category=write_op, rejection_reason) -> pre_reject error + category 写入', () => {
    let t = createTurn('t1', '删除数据');
    t = reduceSseEvent(t, ev({ type: 'stage', data: { query_id: 'q1', node: 'pre_reject', status: 'started' } }));
    t = reduceSseEvent(t, ev({ type: 'stage', data: { query_id: 'q1', node: 'pre_reject', status: 'done', rejection_reason: '本服务仅支持查询', category: 'write_op' } }));
    expect(t.details.preReject?.category).toBe('write_op');
    expect(t.details.preReject?.passed).toBe(false);
    const node = t.timeline.find((n) => n.type === 'pre_reject');
    expect(node?.status).toBe('error');
    // 不生成通用 error 节点
    expect(t.timeline.some((n) => n.type === 'error')).toBe(false);
  });

  test('stage(pre_reject, done, category=normal) -> pre_reject done + category=normal', () => {
    let t = createTurn('t1', '查询');
    t = reduceSseEvent(t, ev({ type: 'stage', data: { query_id: 'q1', node: 'pre_reject', status: 'done', category: 'normal' } }));
    expect(t.details.preReject?.category).toBe('normal');
    expect(t.details.preReject?.passed).toBe(true);
  });

  test('schema_empty 后 stage(schema_finalize, done, rejection_reason) 不重复 upsert 通用 error', () => {
    let t = createTurn('t1', 'q');
    t = reduceSseEvent(t, ev({ type: 'schema_empty', data: { query_id: 'q1', reason: '未找到相关表' } }));
    t = reduceSseEvent(t, ev({ type: 'stage', data: { query_id: 'q1', node: 'schema_finalize', status: 'done', rejection_reason: '未找到相关表' } }));
    // 已有 schema_empty error 节点，不再生成通用 error 节点
    expect(t.timeline.some((n) => n.type === 'error')).toBe(false);
    expect(t.timeline.some((n) => n.type === 'schema_empty' && n.status === 'error')).toBe(true);
  });

  test('schema_empty 后 error(rejection) 事件不重复 upsert 通用 error', () => {
    let t = createTurn('t1', 'q');
    t = reduceSseEvent(t, ev({ type: 'schema_empty', data: { query_id: 'q1', reason: '未找到相关表' } }));
    t = reduceSseEvent(t, ev({ type: 'error', data: { query_id: 'q1', error: '未找到相关表', rejection: true } }));
    expect(t.timeline.some((n) => n.type === 'error')).toBe(false);
    expect(t.timeline.some((n) => n.type === 'schema_empty' && n.status === 'error')).toBe(true);
  });
});

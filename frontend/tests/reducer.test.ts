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

  test('cache 命中短路：点亮 cache 节点，details.cache 填充', () => {
    let t = createTurn('t1', 'q');
    t = reduceSseEvent(
      t,
      ev({
        type: 'cache_check',
        data: { query_id: 'q1', hit: true, source: 'history', confidence: 0.9, cached_sql: 'SELECT 1', recalled: 0 },
      }),
    );
    expect(t.details.cache?.hit).toBe(true);
    expect(t.timeline.find((n) => n.type === 'cache')).toBeTruthy();
  });

  test('cache 未命中不点亮 cache 节点', () => {
    let t = createTurn('t1', 'q');
    t = reduceSseEvent(
      t,
      ev({ type: 'cache_check', data: { query_id: 'q1', hit: false, source: '', confidence: 0, cached_sql: null, recalled: 3 } }),
    );
    expect(t.timeline.find((n) => n.type === 'cache')).toBeFalsy();
  });

  test('keywords + schema_recall -> ir 节点 done + details 填充', () => {
    let t = createTurn('t1', 'q');
    t = reduceSseEvent(t, ev({ type: 'keywords', data: { query_id: 'q1', groups: [{ name: 'k', expansions: ['e'] }] } }));
    t = reduceSseEvent(t, ev({ type: 'schema_recall', data: { query_id: 'q1', groups: [{ name: 'g', top_columns: ['c'] }] } }));
    expect(t.details.keywords?.length).toBe(1);
    expect(t.details.schemaRecall?.[0].top_columns).toEqual(['c']);
    expect(t.timeline.find((n) => n.type === 'ir')?.status).toBe('done');
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
});

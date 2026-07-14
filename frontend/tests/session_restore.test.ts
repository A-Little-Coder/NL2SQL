/**
 * 会话切换恢复测试（change session-restore-event-cache）
 *
 * 覆盖：
 * - 11.1 事件流重放等价性（重放 Turn == 实时累积 Turn，reducer 零改动）
 * - 11.2 turnsBySession 缓存命中/未命中
 * - 11.3 setHistoryTurns 两源兼容（事件流重放 vs 摘要简化重建）
 */
import { createTurn, reduceSseEvent } from '../src/store/reducer';
import { useChatStore } from '../src/store/useChatStore';
import type { SseEvent } from '../src/api/types';

/** 构造任意 SSE 事件（测试用，绕过字面量类型校验） */
function ev(e: { type: string; data: Record<string, unknown> }): SseEvent {
  return e as unknown as SseEvent;
}

/** 一组典型 SSE 事件序列（keywords -> schema_recall -> sql_candidates -> execution -> final_decision -> result -> done） */
function sampleEvents(): SseEvent[] {
  return [
    ev({ type: 'keywords', data: { query_id: 'q1', groups: [{ name: '销售', expansions: ['销售额'] }] } }),
    ev({ type: 'schema_recall', data: { query_id: 'q1', keyword_groups: [{ phrase: '销售', terms: ['销售额'], columns: [{ table: 'sales', column: 'amount', score: 0.9 }], values: [] }] } }),
    ev({ type: 'sql_candidates', data: { query_id: 'q1', candidates: [{ id: 'c1', sql: 'SELECT SUM(amount) FROM sales' }] } }),
    ev({ type: 'execution', data: { query_id: 'q1', candidate_id: 'c1', success: true, rows: 1, error: null } }),
    ev({ type: 'final_decision', data: { query_id: 'q1', selected_id: 'c1', selected_sql: 'SELECT SUM(amount) FROM sales', decision_path: 'r1', fix_failed: false, reason: 'ok' } }),
    ev({ type: 'result', data: { query_id: 'q1', sql: 'SELECT SUM(amount) FROM sales', result: [{ amount: 100 }] } }),
    ev({ type: 'done', data: { query_id: 'q1', has_result: true, awaiting_clarification: false, fix_failed: false, decision_path: 'r1', fix_rounds_used: 0, last_error: null } }),
  ];
}

describe('session-restore-event-cache', () => {
  beforeEach(() => {
    useChatStore.setState({ turns: [], turnsBySession: {}, inspectorTurnId: null, currentSessionId: null });
  });

  test('11.1 事件流重放等价性：重放 Turn 与实时累积 Turn 一致', () => {
    const events = sampleEvents();
    // 实时累积
    const liveTurn = events.reduce((t, e) => reduceSseEvent(t, e), createTurn('live-1', '查销售额'));
    // 重放（setTurnsFromEvents）
    useChatStore.getState().setTurnsFromEvents([{ turn_index: 1, user_query: '查销售额', events }]);
    const replayed = useChatStore.getState().turns;
    expect(replayed).toHaveLength(1);
    const r = replayed[0];
    // 关键字段等价（turnId 不同是预期的：重放用 history-1）
    expect(r.userQuery).toBe(liveTurn.userQuery);
    expect(r.timeline).toEqual(liveTurn.timeline);
    expect(r.details).toEqual(liveTurn.details);
    expect(r.thinking).toEqual(liveTurn.thinking);
    expect(r.result).toEqual(liveTurn.result);
    expect(r.status).toBe(liveTurn.status);
    expect(r.doneMeta).toEqual(liveTurn.doneMeta);
  });

  test('11.1b 重放含 __truncated__ 标记的 result 事件，resultTruncated=true', () => {
    const events = [
      ev({ type: 'result', data: { query_id: 'q1', sql: 'SELECT 1', result: [{ a: 1 }], __truncated__: true } }),
    ];
    useChatStore.getState().setTurnsFromEvents([{ turn_index: 1, events }]);
    expect(useChatStore.getState().turns[0].resultTruncated).toBe(true);
  });

  test('11.1c 未截断的 result 事件，resultTruncated=false', () => {
    const events = [
      ev({ type: 'result', data: { query_id: 'q1', sql: 'SELECT 1', result: [{ a: 1 }] } }),
    ];
    useChatStore.getState().setTurnsFromEvents([{ turn_index: 1, events }]);
    expect(useChatStore.getState().turns[0].resultTruncated).toBe(false);
  });

  test('11.2 turnsBySession 缓存命中', () => {
    const turn = createTurn('t1', 'q');
    useChatStore.setState({ turns: [turn], currentSessionId: 's1' });
    useChatStore.getState().cacheCurrentTurns('s1');
    // 清空 turns（模拟切换离开）
    useChatStore.setState({ turns: [] });
    // 切回 s1 命中缓存
    const hit = useChatStore.getState().loadCachedTurns('s1');
    expect(hit).toBe(true);
    expect(useChatStore.getState().turns).toHaveLength(1);
    expect(useChatStore.getState().turns[0].turnId).toBe('t1');
  });

  test('11.2b turnsBySession 缓存未命中', () => {
    const miss = useChatStore.getState().loadCachedTurns('never-cached');
    expect(miss).toBe(false);
  });

  test('11.3 setHistoryTurns 摘要源简化重建（老会话兼容）', () => {
    useChatStore.getState().setHistoryTurns([
      { user_query: '查A', final_sql: 'SELECT * FROM A', result_meta: { row_count: 5, columns: ['id'] } },
    ]);
    const turns = useChatStore.getState().turns;
    expect(turns).toHaveLength(1);
    expect(turns[0].userQuery).toBe('查A');
    expect(turns[0].result?.sql).toBe('SELECT * FROM A');
  });

  test('11.3b 两源切换：events 源与 summary 源互不干扰', () => {
    // events 源：重放出多节点时间轴
    useChatStore.getState().setTurnsFromEvents([{ turn_index: 1, user_query: 'evt', events: sampleEvents() }]);
    expect(useChatStore.getState().turns).toHaveLength(1);
    expect(useChatStore.getState().turns[0].timeline.length).toBeGreaterThan(1);
    // summary 源覆盖
    useChatStore.getState().setHistoryTurns([{ user_query: 'sum', final_sql: 'SELECT 1' }]);
    expect(useChatStore.getState().turns).toHaveLength(1);
    expect(useChatStore.getState().turns[0].userQuery).toBe('sum');
  });
});

/**
 * resume 合并单测（任务 14.3，决策 D4）
 *
 * 验证：初始流 + resume 流合并到同一 turnId；server query_id 变化不改变 turnId。
 */
import { createTurn, reduceSseEvent } from '../src/store/reducer';
import type { SseEvent } from '../src/api/types';

function ev(e: { type: string; data: Record<string, unknown> }): SseEvent {
  return e as unknown as SseEvent;
}

describe('resume 续流合并到同一 turnId（D4）', () => {
  test('初始流 clarification 挂起 -> resume 流继续，turnId 不变，query_id 更新', () => {
    const turnId = 'client-turn-1';
    let t = createTurn(turnId, '查销售额');

    // 初始流：task_planner 反问挂起
    t = reduceSseEvent(t, ev({ type: 'stage', data: { query_id: 'qid-init', node: 'task_planner', status: 'started' } }));
    t = reduceSseEvent(
      t,
      ev({ type: 'clarification', data: { query_id: 'qid-init', question: '哪个季度?', ambiguities: ['Q1', 'Q2'], round: 1, awaiting_answer: true } }),
    );
    t = reduceSseEvent(
      t,
      ev({ type: 'done', data: { query_id: 'qid-init', has_result: false, awaiting_clarification: true, fix_failed: false, decision_path: '', fix_rounds_used: 0, last_error: null } }),
    );
    expect(t.status).toBe('awaiting_clarification');
    expect(t.queryId).toBe('qid-init');

    // resume 流：server 生成新 query_id，事件并入同一 turnId
    t = reduceSseEvent(t, ev({ type: 'stage', data: { query_id: 'qid-resume', node: 'ir', status: 'started' } }));
    t = reduceSseEvent(t, ev({ type: 'keywords', data: { query_id: 'qid-resume', groups: [{ name: '销售额' }] } }));
    t = reduceSseEvent(t, ev({ type: 'sql_candidates', data: { query_id: 'qid-resume', candidates: [{ id: 'c1', sql: 'SELECT * FROM sales' }] } }));
    t = reduceSseEvent(t, ev({ type: 'result', data: { query_id: 'qid-resume', sql: 'SELECT * FROM sales', result: [{ sales: 100 }] } }));
    t = reduceSseEvent(
      t,
      ev({ type: 'done', data: { query_id: 'qid-resume', has_result: true, awaiting_clarification: false, fix_failed: false, decision_path: 'direct', fix_rounds_used: 0, last_error: null } }),
    );

    // turnId 始终不变（客户端主键）
    expect(t.turnId).toBe(turnId);
    // query_id 更新为 resume 流的最新值
    expect(t.queryId).toBe('qid-resume');
    // 状态收尾为 done
    expect(t.status).toBe('done');
    expect(t.result?.rows.length).toBe(1);
    // 时间轴：反问节点 + resume 后的 result 节点都在
    expect(t.timeline.find((n) => n.type === 'clarify')).toBeTruthy();
    expect(t.timeline.find((n) => n.type === 'result')).toBeTruthy();
  });

  test('多轮反问：两轮 resume 后完成', () => {
    const turnId = 'client-turn-2';
    let t = createTurn(turnId, 'q');

    // 第 1 轮反问
    t = reduceSseEvent(t, ev({ type: 'clarification', data: { query_id: 'q-a', question: 'r1?', ambiguities: [], round: 1, awaiting_answer: true } }));
    t = reduceSseEvent(t, ev({ type: 'done', data: { query_id: 'q-a', has_result: false, awaiting_clarification: true, fix_failed: false, decision_path: '', fix_rounds_used: 0, last_error: null } }));
    expect(t.clarification?.round).toBe(1);

    // 第 2 轮反问（resume 后再次反问）
    t = reduceSseEvent(t, ev({ type: 'clarification', data: { query_id: 'q-b', question: 'r2?', ambiguities: [], round: 2, awaiting_answer: true } }));
    expect(t.clarification?.round).toBe(2);
    expect(t.status).toBe('awaiting_clarification');

    // 第 2 次 resume 完成
    t = reduceSseEvent(t, ev({ type: 'result', data: { query_id: 'q-c', sql: 'SELECT 1', result: [] } }));
    t = reduceSseEvent(t, ev({ type: 'done', data: { query_id: 'q-c', has_result: true, awaiting_clarification: false, fix_failed: false, decision_path: 'direct', fix_rounds_used: 0, last_error: null } }));
    expect(t.status).toBe('done');
    expect(t.turnId).toBe(turnId);
  });
});

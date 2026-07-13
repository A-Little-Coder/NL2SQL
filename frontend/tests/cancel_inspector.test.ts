/**
 * 取消与检查器跨轮锁定单测（change clarify-choice-inspector-cancel）
 *
 * 覆盖：
 * - cancelTurn -> Turn 进 cancelled 终态、timeline 含"用户已取消"、rejection=false、cancelled=true
 * - cancelled 终态不被延迟到达的 done 事件覆盖
 * - inspectorTurnId：点击旧轮节点 pin 该轮；点已选中节点解除；releaseInspector 释放；跨轮保持
 */
import { describe, test, expect, beforeEach } from 'vitest';
import { useChatStore, genTurnId } from '../src/store/useChatStore';
import type { SseEvent } from '../src/api/types';

function ev(e: { type: string; data: Record<string, unknown> }): SseEvent {
  return e as unknown as SseEvent;
}

beforeEach(() => {
  useChatStore.setState({
    turns: [],
    inspectorTurnId: null,
    currentSessionId: null,
    sessions: [],
    selectedDbId: null,
    userId: 'default',
    viewMode: 'chat',
  });
});

describe('cancelTurn（请求终止）', () => {
  test('cancelTurn 使 Turn 进入 cancelled 终态', () => {
    const turnId = genTurnId();
    useChatStore.getState().startTurn(turnId, 'q');
    useChatStore.getState().applyEvent(
      turnId,
      ev({ type: 'keywords', data: { query_id: 'q1', groups: [] } }),
    );
    useChatStore.getState().cancelTurn(turnId);
    const turn = useChatStore.getState().turns[0];
    expect(turn.status).toBe('cancelled');
    expect(turn.cancelled).toBe(true);
    expect(turn.rejection).toBe(false);
    expect(turn.error).toBe('用户已取消请求');
  });

  test('cancelTurn 时间轴追加"用户已取消"节点', () => {
    const turnId = genTurnId();
    useChatStore.getState().startTurn(turnId, 'q');
    useChatStore.getState().cancelTurn(turnId);
    const turn = useChatStore.getState().turns[0];
    const node = turn.timeline.find((n) => n.type === 'error');
    expect(node).toBeTruthy();
    expect(node?.summary).toBe('用户已取消');
  });

  test('cancelled 终态不被延迟到达的 done 事件覆盖', () => {
    const turnId = genTurnId();
    useChatStore.getState().startTurn(turnId, 'q');
    useChatStore.getState().cancelTurn(turnId);
    useChatStore.getState().applyEvent(
      turnId,
      ev({
        type: 'done',
        data: {
          query_id: 'q1',
          has_result: false,
          awaiting_clarification: false,
          fix_failed: false,
          decision_path: '',
          fix_rounds_used: 0,
        },
      }),
    );
    expect(useChatStore.getState().turns[0].status).toBe('cancelled');
  });

  test('cancelTurn 把 active 节点置为 cancelled（停止旋转）', () => {
    const turnId = genTurnId();
    useChatStore.getState().startTurn(turnId, 'q');
    // 模拟一个 active 节点（stage started 但未 done）
    useChatStore.getState().applyEvent(
      turnId,
      ev({ type: 'stage', data: { query_id: 'q1', node: 'ir', status: 'started' } }),
    );
    expect(useChatStore.getState().turns[0].timeline.some((n) => n.status === 'active')).toBe(true);
    useChatStore.getState().cancelTurn(turnId);
    const turn = useChatStore.getState().turns[0];
    // 取消后无 active 节点（不再旋转）
    expect(turn.timeline.some((n) => n.status === 'active')).toBe(false);
    // 原 active 节点变为 cancelled
    expect(turn.timeline.some((n) => n.status === 'cancelled')).toBe(true);
  });

  test('cancelled turn 忽略后续 SSE 事件（不继续追加节点）', () => {
    const turnId = genTurnId();
    useChatStore.getState().startTurn(turnId, 'q');
    useChatStore.getState().applyEvent(
      turnId,
      ev({ type: 'stage', data: { query_id: 'q1', node: 'ir', status: 'started' } }),
    );
    useChatStore.getState().cancelTurn(turnId);
    // 取消后到达的 stage / 业务事件应被忽略，不继续追加节点
    useChatStore.getState().applyEvent(
      turnId,
      ev({ type: 'stage', data: { query_id: 'q1', node: 'cg', status: 'started' } }),
    );
    useChatStore.getState().applyEvent(
      turnId,
      ev({ type: 'sql_candidates', data: { query_id: 'q1', candidates: [{ id: 'c1', sql: 'SELECT 1' }] } }),
    );
    const turn = useChatStore.getState().turns[0];
    expect(turn.status).toBe('cancelled');
    // cg 节点未被追加
    expect(turn.timeline.some((n) => n.type === 'cg')).toBe(false);
    // ir 节点仍在（cancelTurn 前就有）
    expect(turn.timeline.some((n) => n.type === 'ir')).toBe(true);
  });
});

describe('inspectorTurnId（检查器跨轮锁定）', () => {
  test('初始 inspectorTurnId=null（自动跟随最新）', () => {
    expect(useChatStore.getState().inspectorTurnId).toBeNull();
  });

  test('点击节点同时 pin 检查器到该 turn', () => {
    const t1 = genTurnId();
    useChatStore.getState().startTurn(t1, 'q');
    useChatStore.getState().applyEvent(
      t1,
      ev({ type: 'keywords', data: { query_id: 'q1', groups: [] } }),
    );
    useChatStore.getState().selectNode(t1, 'ir');
    expect(useChatStore.getState().inspectorTurnId).toBe(t1);
    expect(useChatStore.getState().turns[0].selectedNode).toBe('ir');
  });

  test('点已选中节点解除 pin（inspectorTurnId 与 selectedNode 同置 null）', () => {
    const t1 = genTurnId();
    useChatStore.getState().startTurn(t1, 'q');
    useChatStore.getState().selectNode(t1, 'ir');
    useChatStore.getState().selectNode(t1, null);
    expect(useChatStore.getState().inspectorTurnId).toBeNull();
    expect(useChatStore.getState().turns[0].selectedNode).toBeNull();
  });

  test('releaseInspector 释放 pin（保留 turn 的 selectedNode）', () => {
    const t1 = genTurnId();
    useChatStore.getState().startTurn(t1, 'q');
    useChatStore.getState().selectNode(t1, 'ir');
    useChatStore.getState().releaseInspector();
    expect(useChatStore.getState().inspectorTurnId).toBeNull();
    expect(useChatStore.getState().turns[0].selectedNode).toBe('ir');
  });

  test('跨轮：pin 旧轮后新轮开始，inspectorTurnId 保持旧轮', () => {
    const t1 = genTurnId();
    useChatStore.getState().startTurn(t1, 'q1');
    useChatStore.getState().selectNode(t1, 'ir');
    const t2 = genTurnId();
    useChatStore.getState().startTurn(t2, 'q2');
    expect(useChatStore.getState().inspectorTurnId).toBe(t1);
  });
});

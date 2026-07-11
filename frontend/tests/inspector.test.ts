/**
 * 检查器选中态单测（任务 14.4，决策 D5）
 *
 * 覆盖：selectedNode 自动跟随（null）/ 点击 pin 锁定 / 新查询重置为 null。
 */
import { useChatStore, genTurnId } from '../src/store/useChatStore';
import type { SseEvent } from '../src/api/types';

function ev(e: { type: string; data: Record<string, unknown> }): SseEvent {
  return e as unknown as SseEvent;
}

beforeEach(() => {
  useChatStore.setState({
    turns: [],
    currentSessionId: null,
    sessions: [],
    selectedDbId: null,
    userId: 'default',
    viewMode: 'chat',
  });
});

describe('检查器选中态（D5）', () => {
  test('新 Turn selectedNode=null（自动跟随最新节点）', () => {
    const turnId = genTurnId();
    useChatStore.getState().startTurn(turnId, 'q');
    useChatStore.getState().applyEvent(turnId, ev({ type: 'keywords', data: { query_id: 'q1', groups: [] } }));
    useChatStore.getState().applyEvent(
      turnId,
      ev({ type: 'sql_candidates', data: { query_id: 'q1', candidates: [{ id: 'c1', sql: 'SELECT 1' }] } }),
    );
    const turn = useChatStore.getState().turns[0];
    expect(turn.selectedNode).toBeNull();
    // 自动跟随 = timeline 最后一个节点（cg）
    expect(turn.timeline[turn.timeline.length - 1].type).toBe('cg');
  });

  test('点击节点 pin -> selectedNode 锁定该节点', () => {
    const turnId = genTurnId();
    useChatStore.getState().startTurn(turnId, 'q');
    useChatStore.getState().applyEvent(turnId, ev({ type: 'keywords', data: { query_id: 'q1', groups: [] } }));
    useChatStore.getState().applyEvent(
      turnId,
      ev({ type: 'sql_candidates', data: { query_id: 'q1', candidates: [{ id: 'c1', sql: 'SELECT 1' }] } }),
    );
    useChatStore.getState().selectNode(turnId, 'ir');
    const turn = useChatStore.getState().turns[0];
    expect(turn.selectedNode).toBe('ir');
  });

  test('解除锁定：selectNode(null) 恢复自动跟随', () => {
    const turnId = genTurnId();
    useChatStore.getState().startTurn(turnId, 'q');
    useChatStore.getState().applyEvent(turnId, ev({ type: 'keywords', data: { query_id: 'q1', groups: [] } }));
    useChatStore.getState().selectNode(turnId, 'ir');
    useChatStore.getState().selectNode(turnId, null);
    expect(useChatStore.getState().turns[0].selectedNode).toBeNull();
  });

  test('新查询重置为自动跟随：新 Turn 的 selectedNode=null，旧 Turn 锁定不受影响', () => {
    const t1 = genTurnId();
    useChatStore.getState().startTurn(t1, 'q1');
    useChatStore.getState().applyEvent(turnId0(t1), ev0('keywords'));
    useChatStore.getState().selectNode(t1, 'ir');
    expect(useChatStore.getState().turns.find((t) => t.turnId === t1)?.selectedNode).toBe('ir');

    // 新查询
    const t2 = genTurnId();
    useChatStore.getState().startTurn(t2, 'q2');
    const newTurn = useChatStore.getState().turns.find((t) => t.turnId === t2);
    expect(newTurn?.selectedNode).toBeNull();
    // 旧 Turn 的 pin 保留
    const oldTurn = useChatStore.getState().turns.find((t) => t.turnId === t1);
    expect(oldTurn?.selectedNode).toBe('ir');
  });
});

// ---- 测试辅助（避免重复样板）----
function turnId0(id: string): string {
  return id;
}
function ev0(type: string): SseEvent {
  return ev({ type, data: { query_id: 'q1', groups: [] } });
}

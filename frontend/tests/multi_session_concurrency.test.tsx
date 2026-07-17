/**
 * multi-session-concurrency 前台并行化测试
 *
 * - 5.1: applyEventToSession 按 session 路由、后台会话流持续更新、切回见推进
 * - 5.2: cancelTurn 隔离（取消当前会话 Turn 不影响后台会话在途 Turn）
 * - 5.3: 跨会话并行（getRunningSessionIds 含多个在途）+ Conversation 单会话单在途 UI（停止/发送钮、输入不禁用）
 */
import { describe, test, expect, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { useChatStore, genTurnId } from '../src/store/useChatStore';
import type { SseEvent } from '../src/api/types';
import Conversation from '../src/components/Conversation';

// jsdom 未实现 scrollIntoView，Conversation 自动滚动 useEffect 需要，mock 之
Element.prototype.scrollIntoView = () => {};

function ev(e: { type: string; data: Record<string, unknown> }): SseEvent {
  return e as unknown as SseEvent;
}

beforeEach(() => {
  useChatStore.setState({
    turns: [],
    turnsBySession: {},
    inspectorTurnId: null,
    currentSessionId: null,
    sessions: [],
    selectedDbId: null,
    userId: 'default',
    viewMode: 'chat',
    loadingDb: false,
  });
});

describe('5.1 applyEventToSession 按 session 路由', () => {
  test('当前会话事件更新 turns', () => {
    useChatStore.setState({ currentSessionId: 'A', selectedDbId: 'db' });
    const tA = genTurnId();
    useChatStore.getState().startTurn(tA, 'q');
    useChatStore
      .getState()
      .applyEventToSession('A', tA, ev({ type: 'stage', data: { query_id: 'q1', node: 'ir', status: 'started' } }));
    expect(useChatStore.getState().turns[0].timeline.some((n) => n.type === 'ir')).toBe(true);
  });

  test('后台会话事件更新 turnsBySession，不影响当前 turns', () => {
    useChatStore.setState({ currentSessionId: 'A', selectedDbId: 'db' });
    const tA = genTurnId();
    useChatStore.getState().startTurn(tA, 'q');
    useChatStore
      .getState()
      .applyEventToSession('A', tA, ev({ type: 'stage', data: { query_id: 'q1', node: 'ir', status: 'started' } }));
    // 切到 B（缓存 A）
    useChatStore.getState().cacheCurrentTurns('A');
    useChatStore.setState({ currentSessionId: 'B', turns: [] });
    // A 后台事件
    useChatStore
      .getState()
      .applyEventToSession('A', tA, ev({ type: 'stage', data: { query_id: 'q1', node: 'ir', status: 'done' } }));
    // 当前 turns（B）不受影响
    expect(useChatStore.getState().turns).toHaveLength(0);
    // turnsBySession[A] 的 ir 节点已 done
    const aTurns = useChatStore.getState().turnsBySession['A'];
    expect(aTurns[0].timeline.some((n) => n.type === 'ir' && n.status === 'done')).toBe(true);
  });

  test('切回后台会话见推进态（不停滞在切走瞬间）', () => {
    useChatStore.setState({ currentSessionId: 'A', selectedDbId: 'db' });
    const tA = genTurnId();
    useChatStore.getState().startTurn(tA, 'q');
    useChatStore
      .getState()
      .applyEventToSession('A', tA, ev({ type: 'stage', data: { query_id: 'q1', node: 'ir', status: 'started' } }));
    useChatStore.getState().cacheCurrentTurns('A');
    useChatStore.setState({ currentSessionId: 'B', turns: [] });
    // 后台持续推进
    useChatStore
      .getState()
      .applyEventToSession('A', tA, ev({ type: 'stage', data: { query_id: 'q1', node: 'ir', status: 'done' } }));
    // 切回 A
    useChatStore.getState().cacheCurrentTurns('B');
    useChatStore.getState().loadCachedTurns('A');
    useChatStore.setState({ currentSessionId: 'A' });
    const turn = useChatStore.getState().turns[0];
    expect(turn.status).toBe('streaming');
    expect(turn.timeline.some((n) => n.type === 'ir' && n.status === 'done')).toBe(true);
  });
});

describe('5.2 cancelTurn 隔离', () => {
  test('取消当前会话 B 的 Turn 不影响后台会话 A 的在途 Turn', () => {
    useChatStore.setState({ currentSessionId: 'A', selectedDbId: 'db' });
    const tA = genTurnId();
    useChatStore.getState().startTurn(tA, 'qA');
    useChatStore.getState().cacheCurrentTurns('A');
    useChatStore.setState({ currentSessionId: 'B', turns: [] });
    const tB = genTurnId();
    useChatStore.getState().startTurn(tB, 'qB');
    // A、B 均在途
    expect(useChatStore.getState().getRunningSessionIds().sort()).toEqual(['A', 'B']);
    // 取消 B（当前会话）
    useChatStore.getState().cancelTurn(tB);
    expect(useChatStore.getState().turns[0].status).toBe('cancelled');
    // A 后台在途不受影响
    expect(useChatStore.getState().turnsBySession['A'][0].status).toBe('streaming');
    expect(useChatStore.getState().getRunningSessionIds()).toEqual(['A']);
  });
});

describe('5.3 跨会话并行 + 单会话单在途 UI', () => {
  test('getRunningSessionIds 同时含多个在途会话（跨会话并行）', () => {
    useChatStore.setState({ currentSessionId: 'A', selectedDbId: 'db' });
    useChatStore.getState().startTurn(genTurnId(), 'qA');
    useChatStore.getState().cacheCurrentTurns('A');
    useChatStore.setState({ currentSessionId: 'B', turns: [] });
    useChatStore.getState().startTurn(genTurnId(), 'qB');
    expect(useChatStore.getState().getRunningSessionIds().sort()).toEqual(['A', 'B']);
  });

  test('当前会话在途时显示"停止"钮、输入框不禁用（单会话单在途 + 跨会话不互斥）', () => {
    useChatStore.setState({ currentSessionId: 'A', selectedDbId: 'db' });
    useChatStore.getState().startTurn(genTurnId(), 'q'); // streaming
    render(<Conversation />);
    expect(screen.getByRole('button', { name: /停止/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /发送/ })).toBeNull();
    const input = screen.getByRole('textbox') as HTMLTextAreaElement;
    expect(input.disabled).toBe(false);
  });

  test('当前会话无在途时显示"发送"钮', () => {
    useChatStore.setState({ currentSessionId: 'A', selectedDbId: 'db' });
    render(<Conversation />);
    expect(screen.getByRole('button', { name: /发送/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /停止/ })).toBeNull();
  });
});

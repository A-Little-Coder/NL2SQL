/**
 * D4/D5（change: enhance-ir-display-and-layout）: IrDetail / SsDetail 渲染测试
 *
 * 覆盖：
 * - IrDetail 按关键词组渲染 phrase/同义词/字段/值
 * - 空召回组展示"无召回"占位
 * - SsDetail 渲染 join_edges/bridge_tables；无数据时显示进行中提示
 */
import { describe, test, expect, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import DetailInspector from '../src/components/DetailInspector';
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

describe('IrDetail 按关键词组聚合渲染（D5）', () => {
  test('单组渲染 phrase/同义词/字段/值', () => {
    const turnId = genTurnId();
    useChatStore.getState().startTurn(turnId, '各科score');
    useChatStore.getState().applyEvent(turnId, ev({
      type: 'schema_recall',
      data: {
        query_id: 'q1',
        keyword_groups: [{
          phrase: '各科score',
          terms: ['各科score', 'subject score'],
          columns: [{ table: 'satscores', column: 'AvgScrRead', score: 0.92 }],
          values: [{ value: 'Lincoln High', table: 'schools', column: 'school_name', score: 0.78 }],
        }],
      },
    }));
    useChatStore.getState().selectNode(turnId, 'ir');

    render(<DetailInspector />);

    // phrase 出现在 panel header + terms Tag（Collapse 默认展开首组）
    expect(screen.getAllByText('各科score').length).toBeGreaterThan(0);
    expect(screen.getByText(/subject score/)).toBeInTheDocument();
    expect(screen.getByText(/AvgScrRead/)).toBeInTheDocument();
    expect(screen.getByText(/Lincoln High/)).toBeInTheDocument();
  });

  test('空召回组展示"无召回"占位', () => {
    const turnId = genTurnId();
    useChatStore.getState().startTurn(turnId, 'q');
    useChatStore.getState().applyEvent(turnId, ev({
      type: 'schema_recall',
      data: {
        query_id: 'q1',
        keyword_groups: [{
          phrase: '空组',
          terms: ['空组'],
          columns: [],
          values: [],
        }],
      },
    }));
    useChatStore.getState().selectNode(turnId, 'ir');

    render(<DetailInspector />);

    expect(screen.getAllByText('空组').length).toBeGreaterThan(0);
    // 字段和值都为空 -> 各显示一个"无召回"
    expect(screen.getAllByText('无召回')).toHaveLength(2);
  });

  test('多组各 phrase 在 panel header 可见', () => {
    const turnId = genTurnId();
    useChatStore.getState().startTurn(turnId, '各科score和学校总数');
    useChatStore.getState().applyEvent(turnId, ev({
      type: 'schema_recall',
      data: {
        query_id: 'q1',
        keyword_groups: [
          { phrase: '各科score', terms: ['各科score'], columns: [], values: [] },
          { phrase: '学校总数', terms: ['学校总数'], columns: [], values: [] },
        ],
      },
    }));
    useChatStore.getState().selectNode(turnId, 'ir');

    render(<DetailInspector />);

    // 两组 phrase 都在 header 渲染（即使第二组未展开）
    expect(screen.getAllByText('各科score').length).toBeGreaterThan(0);
    expect(screen.getAllByText('学校总数').length).toBeGreaterThan(0);
  });
});

describe('SsDetail 渲染（D4）', () => {
  test('schema_finalize 数据渲染 join_edges/bridge_tables', () => {
    const turnId = genTurnId();
    useChatStore.getState().startTurn(turnId, 'q');
    useChatStore.getState().applyEvent(turnId, ev({ type: 'stage', data: { query_id: 'q1', node: 'ss', status: 'done' } }));
    useChatStore.getState().applyEvent(turnId, ev({ type: 'schema_finalize', data: { join_edges: 3, bridge_tables: 1 } }));
    useChatStore.getState().selectNode(turnId, 'ss');

    render(<DetailInspector />);

    expect(screen.getByText('3')).toBeInTheDocument();
    expect(screen.getByText('1')).toBeInTheDocument();
  });

  test('无 schema_finalize 数据时显示进行中提示', () => {
    const turnId = genTurnId();
    useChatStore.getState().startTurn(turnId, 'q');
    useChatStore.getState().applyEvent(turnId, ev({ type: 'stage', data: { query_id: 'q1', node: 'ss', status: 'done' } }));
    useChatStore.getState().selectNode(turnId, 'ss');

    render(<DetailInspector />);

    expect(screen.getByText(/SS 阶段进行中/)).toBeInTheDocument();
  });
});

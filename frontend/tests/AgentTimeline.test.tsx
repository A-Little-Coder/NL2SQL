/**
 * AgentTimeline cache 节点命中展示测试（change: cache-hit-ux-and-layout）
 *
 * 覆盖：
 * - metric_definition 命中带指标名：摘要"长期记忆·{name}" + "查看"入口
 * - metric_definition 命中无指标名：摘要"长期记忆"，无"查看"入口
 * - session_history 命中：摘要"会话历史·{query}"，无"查看"入口
 */
import { describe, test, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import AgentTimeline from '../src/components/AgentTimeline';
import { createTurn } from '../src/store/reducer';
import type { Turn, TurnDetails } from '../src/store/types';

/** 构造一个 cache 命中的 turn：details.cache 填充 + timeline 仅一个 cache 节点 */
function makeCacheTurn(
  cache: NonNullable<TurnDetails['cache']>,
  summary: string,
): Turn {
  const t = createTurn('t1', '查询');
  t.details = { ...t.details, cache };
  t.timeline = [{ type: 'cache', status: 'done', summary }];
  return t;
}

describe('AgentTimeline cache 节点命中展示', () => {
  test('metric_definition 命中带指标名：显示"长期记忆·{name}" + "查看"入口', () => {
    const turn = makeCacheTurn(
      {
        hit: true,
        source: 'metric_definition',
        confidence: 0.92,
        cachedSql: 'SELECT SUM(amount) FROM sales',
        matchedMetricName: '销售额',
        historicalQuery: null,
      },
      '长期记忆·销售额 · conf=0.92',
    );
    render(<AgentTimeline turn={turn} />);
    expect(screen.getByText(/长期记忆·销售额/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '查看' })).toBeInTheDocument();
  });

  test('metric_definition 命中无指标名：显示"长期记忆"，无"查看"入口', () => {
    const turn = makeCacheTurn(
      {
        hit: true,
        source: 'metric_definition',
        confidence: 0.92,
        cachedSql: 'SELECT 1',
        matchedMetricName: null,
        historicalQuery: null,
      },
      '长期记忆 · conf=0.92',
    );
    render(<AgentTimeline turn={turn} />);
    expect(screen.getByText(/长期记忆/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '查看' })).toBeNull();
  });

  test('session_history 命中：显示"会话历史·{query}"，无"查看"入口', () => {
    const turn = makeCacheTurn(
      {
        hit: true,
        source: 'session_history',
        confidence: 0.95,
        cachedSql: 'SELECT 1',
        matchedMetricName: null,
        historicalQuery: '查询苹果的销售额',
      },
      '会话历史·查询苹果的销售额 · conf=0.95',
    );
    render(<AgentTimeline turn={turn} />);
    expect(screen.getByText(/会话历史·查询苹果的销售额/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '查看' })).toBeNull();
  });
});

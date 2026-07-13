/**
 * ClarificationBubble 按 kind 渲染测试（change clarify-choice-inspector-cancel）
 *
 * 覆盖：
 * - confirm：渲染两个按钮，无输入框
 * - choice：选项按钮 + 输入框
 * - open：纯输入框
 * - kind 缺失/null：回退 open（纯输入框）
 */
import { describe, test, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import ClarificationBubble from '../src/components/ClarificationBubble';
import { createTurn } from '../src/store/reducer';
import type { Turn } from '../src/store/types';

function makeClarifyTurn(clarification: Turn['clarification']): Turn {
  const t = createTurn('t1', 'q');
  t.status = 'awaiting_clarification';
  t.clarification = clarification;
  return t;
}

describe('ClarificationBubble 按 kind 渲染', () => {
  test('confirm：渲染两个按钮，无输入框', () => {
    const turn = makeClarifyTurn({
      question: '是否复用？',
      ambiguities: [],
      round: 1,
      kind: 'confirm',
      options: [
        { label: '是，复用', value: 'yes' },
        { label: '否，重新生成', value: 'no' },
      ],
    });
    render(<ClarificationBubble turn={turn} />);
    expect(screen.getByText('是，复用')).toBeInTheDocument();
    expect(screen.getByText('否，重新生成')).toBeInTheDocument();
    expect(screen.queryByPlaceholderText('输入你的回答…')).toBeNull();
  });

  test('choice：渲染选项按钮 + 输入框', () => {
    const turn = makeClarifyTurn({
      question: '选择哪个？',
      ambiguities: [],
      round: 1,
      kind: 'choice',
      options: [
        { label: '选项A', value: 'a' },
        { label: '选项B', value: 'b' },
      ],
    });
    render(<ClarificationBubble turn={turn} />);
    expect(screen.getByText('选项A')).toBeInTheDocument();
    expect(screen.getByText('选项B')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('输入你的回答…')).toBeInTheDocument();
  });

  test('open：纯输入框，无选项按钮', () => {
    const turn = makeClarifyTurn({
      question: '请补充',
      ambiguities: [],
      round: 1,
      kind: 'open',
    });
    render(<ClarificationBubble turn={turn} />);
    expect(screen.getByPlaceholderText('输入你的回答…')).toBeInTheDocument();
  });

  test('kind 缺失（null）：回退 open（纯输入框）', () => {
    const turn = makeClarifyTurn({
      question: '请补充',
      ambiguities: [],
      round: 1,
      kind: null,
    });
    render(<ClarificationBubble turn={turn} />);
    expect(screen.getByPlaceholderText('输入你的回答…')).toBeInTheDocument();
  });
});

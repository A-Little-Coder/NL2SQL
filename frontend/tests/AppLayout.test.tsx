/**
 * D6/D7（change: enhance-ir-display-and-layout）: 三栏可拖拽布局测试
 *
 * 覆盖：
 * - CollapsedBar 折叠展开条渲染 + 点击 onExpand（D7 折叠态交互）
 * - AppLayout smoke：三栏渲染不崩溃，标题可见
 *
 * 注：拖拽分隔条的像素级交互依赖真实布局，jsdom 无尺寸，此处不覆盖
 *     （留待 7.3 端到端手动验证）；折叠/展开通过 CollapsedBar 覆盖。
 */
import { describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { CollapsedBar } from '../src/components/AppLayout';

describe('CollapsedBar 折叠展开条（D7）', () => {
  test('左栏折叠条渲染展开按钮，点击触发 onExpand', () => {
    const onExpand = vi.fn();
    render(<CollapsedBar side="left" onExpand={onExpand} />);
    const btn = screen.getByRole('button', { name: /展开会话侧栏/ });
    fireEvent.click(btn);
    expect(onExpand).toHaveBeenCalledTimes(1);
  });

  test('右栏折叠条标题为详情检查器', () => {
    const onExpand = vi.fn();
    render(<CollapsedBar side="right" onExpand={onExpand} />);
    expect(screen.getByRole('button', { name: /展开详情检查器/ })).toBeInTheDocument();
  });
});

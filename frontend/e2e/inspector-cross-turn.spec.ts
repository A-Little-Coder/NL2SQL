/**
 * E2E：检查器跨轮查看（change clarify-choice-inspector-cancel）
 *
 * 场景：第1轮完成后点击其节点 -> 检查器锁定到第1轮 -> 发起第2轮 ->
 *      检查器仍显示第1轮（锁定） -> 点"返回最新" -> 检查器切到第2轮。
 */
import { test, expect } from '@playwright/test';

test('检查器跨轮锁定与返回最新', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByPlaceholder(/输入问题|请先/)).toBeVisible({ timeout: 20000 });

  const input = page.getByPlaceholder(/输入问题/).first();

  // 第 1 轮
  await input.fill('查询销售额');
  await page.getByRole('button', { name: '发送' }).click();
  // 等待第1轮完成（结果/拒答/反问任一终态）
  await page.waitForTimeout(10000);

  // 点击第1轮时间轴的某个节点（如"信息检索"或"前置检查"），锁定检查器
  // AgentTimeline 节点带中文标签；点第一个可见节点
  const firstTurnCard = page.locator('.ant-card').first();
  const nodeLabel = firstTurnCard.getByText(/前置检查|信息检索|缓存|改写/).first();
  await nodeLabel.click();

  // 检查器应显示"已锁定到第 1 轮" + "返回最新"按钮（此时只有1轮，可能不显示返回最新；
  // 发起第2轮后应显示）
  // 发起第 2 轮
  await input.fill('查询订单数');
  await page.getByRole('button', { name: '发送' }).click();

  // 期望检查器显示"已锁定到第 1 轮"（pin 在旧轮，不跟新轮）
  await expect(page.getByText(/已锁定到第 1 轮/)).toBeVisible({ timeout: 10000 });

  // 点"返回最新" -> 检查器切到最新轮
  await page.getByRole('button', { name: '返回最新' }).click();
  // "已锁定到第 1 轮"提示应消失
  await expect(page.getByText(/已锁定到第 1 轮/)).toBeHidden({ timeout: 5000 });
});

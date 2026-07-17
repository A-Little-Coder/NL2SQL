/**
 * E2E：跨会话并发不互斥（change multi-session-concurrency，精简单用例）
 *
 * 场景：会话 A 发起较慢查询进入流式 -> 切到新会话 B -> B 输入框可用、发送钮可见
 *      （跨会话不互斥，前台不再全局卡输入）-> B 发起查询并独立完成。
 *
 * 前置：后端已起（python run_api.py，:8000），已选库，QUERY_MAX_CONCURRENCY ≥ 2。
 * 注：A 后台持续推进 / 切回见推进态 由 Vitest 5.1 覆盖；本 e2e 聚焦用户可见的
 *     "A 流式中 B 仍可输入发送"这一核心并发不互斥行为。
 */
import { test, expect } from '@playwright/test';

test('跨会话并发不互斥：A 流式中切到 B 可输入发送，B 独立完成', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByPlaceholder(/输入问题|请先/)).toBeVisible({ timeout: 20000 });

  const input = page.getByPlaceholder(/输入问题/).first();
  // 会话 A：发起一条会走完整 IR/CG 链路的较慢查询
  await input.fill('查询近30天销售额Top10的商品及其库存情况');
  await page.getByRole('button', { name: '发送' }).click();

  // A 进入流式（停止钮出现）；查询过快则跳过
  const stopBtn = page.getByRole('button', { name: '停止' });
  const aStreaming = await stopBtn.isVisible({ timeout: 10000 }).catch(() => false);
  if (!aStreaming) {
    test.skip(true, '会话 A 查询过快结束，未捕捉到流式窗口，无法验证并发不互斥');
  }

  // 切到新会话 B（侧栏"新会话"）
  await page.getByRole('button', { name: '新会话' }).click();

  // 核心：A 仍在流式，但 B 的输入框可用、发送钮可见（跨会话不互斥）
  const bInput = page.getByPlaceholder(/输入问题/).first();
  await expect(bInput).toBeEnabled({ timeout: 5000 });
  await expect(page.getByRole('button', { name: '发送' })).toBeVisible();

  // B 发起查询 -> 流式（停止钮）-> 独立完成（发送钮恢复）
  await bInput.fill('查询销量最高的商品');
  await page.getByRole('button', { name: '发送' }).click();
  await expect(page.getByRole('button', { name: '停止' })).toBeVisible({ timeout: 10000 });
  await expect(page.getByRole('button', { name: '发送' })).toBeVisible({ timeout: 60000 });
});

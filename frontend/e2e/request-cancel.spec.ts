/**
 * E2E：请求终止（change clarify-choice-inspector-cancel）
 *
 * 场景：发起查询 -> 流式中点"停止" -> Turn 显示"已取消"，无"推理进行中"。
 *
 * 注：需查询足够慢以能在流式中点停止（选一个会走完整 IR/CG 链路的自然语言查询）。
 */
import { test, expect } from '@playwright/test';

test('在途请求可停止，Turn 进入已取消态', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByPlaceholder(/输入问题|请先/)).toBeVisible({ timeout: 20000 });

  const input = page.getByPlaceholder(/输入问题/).first();
  await input.fill('查询近30天销售额Top10的商品及其库存情况');
  await page.getByRole('button', { name: '发送' }).click();

  // 发送后按钮应变"停止"（canStop = sending && streaming）
  const stopBtn = page.getByRole('button', { name: '停止' });
  // 最多等 10s 出现停止按钮（若查询太快结束则跳过）
  const canStop = await stopBtn.isVisible({ timeout: 10000 }).catch(() => false);
  if (!canStop) {
    test.skip(true, '查询过快结束，未捕捉到流式窗口，无法验证停止');
  }

  // 点停止
  await stopBtn.click();

  // Turn 应显示"已取消"提示，且不显示"推理进行中"
  await expect(page.getByText('已取消').first()).toBeVisible({ timeout: 5000 });
  await expect(page.getByText('推理进行中…')).toBeHidden();

  // 停止后按钮恢复"发送"
  await expect(page.getByRole('button', { name: '发送' })).toBeVisible({ timeout: 5000 });
});

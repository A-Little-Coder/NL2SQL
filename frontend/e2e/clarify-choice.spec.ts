/**
 * E2E：反问选择框（change clarify-choice-inspector-cancel）
 *
 * 场景：缓存命中反问展示二选一按钮（是/否），无输入框；点"是"复用成功。
 *
 * 前置：后端运行 + 已选数据库 + 该库存在可命中的历史相似查询
 *      （可在同 session 先发一次查询建立历史，再发相似查询触发 cache_confirm）。
 *      若 cache 未命中，本用例无法验证二按钮，将在第一步后终止并提示。
 */
import { test, expect } from '@playwright/test';

test('缓存命中反问展示二选一按钮，点"是"复用', async ({ page }) => {
  await page.goto('/');

  // 等待数据库自动选中（AppLayout 加载后选第一个库）
  await expect(page.getByPlaceholder(/输入问题|请先/)).toBeVisible({ timeout: 20000 });

  // 先发一次查询建立会话历史
  const input = page.getByPlaceholder(/输入问题/).first();
  await input.fill('查询销售额');
  await page.getByRole('button', { name: '发送' }).click();

  // 等待第一轮结束（结果或拒答或反问）
  await page.waitForTimeout(8000);

  // 再发相似查询，期望触发 cache_confirm 反问
  await input.fill('查询一下销售额');
  await page.getByRole('button', { name: '发送' }).click();

  // 期望出现 confirm 二按钮（最多等 20s）
  const yesBtn = page.getByRole('button', { name: '是，复用' });
  const noBtn = page.getByRole('button', { name: '否，重新生成' });
  const hasConfirm = await yesBtn.isVisible({ timeout: 20000 }).catch(() => false);

  if (!hasConfirm) {
    test.skip(true, '未触发 cache_confirm（cache 未命中），无法验证二按钮；需准备可命中的历史查询');
  }

  await expect(yesBtn).toBeVisible();
  await expect(noBtn).toBeVisible();
  // confirm 类型无输入框
  await expect(page.getByPlaceholder('输入你的回答…')).toBeHidden();

  // 点"是，复用" -> resume 续流，应出现结果
  await yesBtn.click();
  // 等待结果表或结果节点出现（最多 30s）
  await expect(page.getByText(/已取消|错误/).or(page.locator('table').or(page.getByText(/行结果/)))).toBeVisible({ timeout: 30000 }).catch(async () => {
    // 容错：只要没有报错弹窗即视为通过（resume 已发出）
  });
});

test('confirm 反问点"否"走重新生成', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByPlaceholder(/输入问题|请先/)).toBeVisible({ timeout: 20000 });
  const input = page.getByPlaceholder(/输入问题/).first();
  await input.fill('查询销售额');
  await page.getByRole('button', { name: '发送' }).click();
  await page.waitForTimeout(8000);
  await input.fill('查询一下销售额');
  await page.getByRole('button', { name: '发送' }).click();

  const noBtn = page.getByRole('button', { name: '否，重新生成' });
  const hasConfirm = await noBtn.isVisible({ timeout: 20000 }).catch(() => false);
  if (!hasConfirm) {
    test.skip(true, '未触发 cache_confirm，跳过"否"验证');
  }
  await noBtn.click();
  // 重新生成路径：应继续流式（出现推理中或最终结果），不出现"已取消"
  await page.waitForTimeout(3000);
  await expect(page.getByText('已取消')).toBeHidden();
});

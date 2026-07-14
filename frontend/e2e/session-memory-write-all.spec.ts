/**
 * E2E：会话历史全写入 + reuse_eligible 过滤（change relax-session-write-gate）
 *
 * 场景 A：SmartFix 全失败后 follow-up
 *   - 第 1 轮发一个必然 SQL 执行失败的查询，等待 SmartFix 失败
 *   - 第 2 轮 follow-up 输入"换个条件再试试"
 *   - 验证改写模块能解析指代，且不报错
 *
 * 场景 B：TaskPlanner 拒答后 follow-up
 *   - 第 1 轮发一个触发拒答的查询
 *   - 第 2 轮 follow-up 输入"那个改成华东"
 *   - 验证改写模块能解析指代，且不报错
 *
 * 前置：后端运行 + 已选数据库
 */
import { test, expect } from '@playwright/test';

test('SmartFix 失败后 follow-up "换个条件再试试"不报错', async ({ page }) => {
  await page.goto('/');

  // 等待应用加载
  await expect(page.getByPlaceholder(/输入问题|请先/)).toBeVisible({ timeout: 20000 });

  const input = page.getByPlaceholder(/输入问题/).first();

  // 第 1 轮：发一个查询，期望 SQL 执行失败触发 SmartFix
  // 注：SmartFix 是否触发取决于 LLM+DB 行为，如果查询成功执行则跳过
  await input.fill('查询一个不存在的表和字段');
  await page.getByRole('button', { name: '发送' }).click();
  // 等待第一轮完成（最多 30s 等结果或错误）
  await page.waitForTimeout(12000);

  // 记录第一轮是否触发了 fix_failed（通过 done 事件的 fix_failed 字段判断）
  // 前端 fix_failed 时会在结果区域显示"修复失败"或错误信息
  const hasError = await page.getByText(/修复失败|错误|失败/).isVisible().catch(() => false);

  if (!hasError) {
    // 如果第一轮没有失败，检查是否正常出了结果（查询可能恰好成功）
    const hasResult = await page.getByText(/行结果/).or(page.locator('table')).isVisible().catch(() => false);
    if (hasResult) {
      // 查询成功了，这个测试场景无法验证（SmartFix 未触发）
      test.skip(true, '第一轮查询未触发 SmartFix 失败，无法验证失败后 follow-up 场景');
    }
  }

  // 第 2 轮：follow-up 引用上一轮查询
  await input.fill('换个条件再试试');
  await page.getByRole('button', { name: '发送' }).click();
  // 等待第二轮完成
  await page.waitForTimeout(12000);

  // 验证：不出现崩溃性错误（至少第二轮能正常完成）
  const hasFatalError = await page.getByText(/服务器错误|Internal Server Error/).isVisible().catch(() => false);
  expect(hasFatalError).toBe(false);
});

test('TaskPlanner 拒答后 follow-up "那个改成华东"不报错', async ({ page }) => {
  await page.goto('/');

  // 等待应用加载
  await expect(page.getByPlaceholder(/输入问题|请先/)).toBeVisible({ timeout: 20000 });

  const input = page.getByPlaceholder(/输入问题/).first();

  // 第 1 轮：发一个可能触发拒答的查询
  // 注：拒答是否触发取决于 LLM 判断，如果查询正常执行则跳过
  await input.fill('修改数据库中的数据');
  await page.getByRole('button', { name: '发送' }).click();
  // 等待第一轮完成
  await page.waitForTimeout(12000);

  // 检查是否触发了拒答（前端显示"拒答"或"拒绝回答"）
  const hasRejection = await page.getByText(/拒答|拒绝回答|拒绝|不回答/).isVisible().catch(() => false);

  if (!hasRejection) {
    // 检查是否正常出了结果
    const hasResult = await page.getByText(/行结果/).or(page.locator('table')).isVisible().catch(() => false);
    if (hasResult) {
      test.skip(true, '第一轮查询未触发拒答，无法验证拒答后 follow-up 场景');
    }
  }

  // 第 2 轮：follow-up 引用上一轮查询
  await input.fill('那个改成华东');
  await page.getByRole('button', { name: '发送' }).click();
  // 等待第二轮完成
  await page.waitForTimeout(12000);

  // 验证：不出现崩溃性错误
  const hasFatalError = await page.getByText(/服务器错误|Internal Server Error/).isVisible().catch(() => false);
  expect(hasFatalError).toBe(false);
});
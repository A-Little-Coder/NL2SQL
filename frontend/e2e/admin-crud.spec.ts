import { test, expect } from '@playwright/test';

/**
 * 权限管理后台 E2E（task 9.5）
 *
 * 前置：后端已起（TABLE_FIELD_ACL_ENABLED=true python run_api.py），auth.db 已 init。
 * 覆盖：新增角色 -> 员工 -> 黑名单规则 -> 列表验证 -> 有效权限查询 -> 删除。
 * 用唯一 rid（Date.now）避免累积；:visible + hasText 过滤可见行，不依赖 active tab class。
 */

test('后台 CRUD 全流程：角色/员工/黑名单/权限查询', async ({ page }) => {
  await page.goto('/admin');
  await expect(page.getByText('权限管理后台')).toBeVisible();
  const rid = 'e2e_' + Date.now();
  const uid = rid + '_u';
  const visibleRowWith = (text: string) =>
    page.locator('.ant-table-row:visible').filter({ hasText: text });

  // ── 角色管理 ──
  await page.getByRole('tab', { name: '角色管理' }).click();
  const roleForm = page.locator('[data-testid=role-form]');
  await roleForm.getByLabel('角色ID').fill(rid);
  await roleForm.getByLabel('名称').fill('E2E角色');
  await roleForm.locator('button[type=submit]').click();
  await expect(visibleRowWith(rid)).toBeVisible({ timeout: 15000 });

  // ── 员工管理 ──
  await page.getByRole('tab', { name: '员工管理' }).click();
  const userForm = page.locator('[data-testid=user-form]');
  await userForm.getByLabel('员工ID').fill(uid);
  await userForm.getByLabel('姓名').fill('E2E用户');
  await userForm.locator('button[type=submit]').click();
  await expect(visibleRowWith(uid)).toBeVisible({ timeout: 15000 });

  // ── 黑名单配置 ──
  await page.getByRole('tab', { name: '黑名单配置' }).click();
  const ruleForm = page.locator('[data-testid=rule-form]');
  await ruleForm.getByLabel('库ID').fill('california_schools');
  await ruleForm.getByLabel('角色ID').fill(rid);
  await ruleForm.getByLabel('表模式').fill('schools');
  await ruleForm.getByLabel('列模式').fill('Latitude');
  await ruleForm.locator('button[type=submit]').click();
  await expect(visibleRowWith(rid)).toBeVisible({ timeout: 15000 });

  // ── 有效权限查询：uid 未绑角色，应为空 ──
  await page.getByRole('tab', { name: '有效权限查询' }).click();
  const permsForm = page.locator('[data-testid=perms-form]');
  await permsForm.getByPlaceholder('员工ID').fill(uid);
  await permsForm.getByPlaceholder('库ID').fill('california_schools');
  await permsForm.getByRole('button').click();
  await expect(page.locator('.ant-empty:visible')).toBeVisible({ timeout: 15000 });

  // ── 删除黑名单规则 ──
  await page.getByRole('tab', { name: '黑名单配置' }).click();
  await page.locator('.ant-btn-dangerous:visible').first().click();
});

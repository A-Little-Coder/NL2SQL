/**
 * 权限管理 REST 接口封装（table-field-acl）
 *
 * 镜像后端 src/api/routes/admin.py，走同源 /api/v1/admin/...。
 */
const BASE = '/api/v1';

export interface Role {
  role_id: string;
  name: string;
}

export interface AdminUser {
  user_id: string;
  name: string;
  dept: string | null;
}

export interface DenyRule {
  id?: number;
  db_id: string;
  role_id: string;
  table_pattern: string;
  column_pattern: string | null; // null = 整表禁
  reason: string | null;
}

export interface EffectivePermission {
  table_pattern: string;
  column_pattern: string | null;
  reason: string | null;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
  const text = await res.text();
  let data: unknown = {};
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { error: text };
    }
  }
  if (!res.ok) {
    const errObj = (data && typeof data === 'object' ? data : {}) as { error?: string };
    throw new Error(errObj.error || `HTTP ${res.status}`);
  }
  return data as T;
}

/** GET /admin/roles */
export const listRoles = () =>
  request<{ roles: Role[] }>(`${BASE}/admin/roles`);

/** POST /admin/roles */
export const createRole = (body: Role) =>
  request<{ ok: boolean }>(`${BASE}/admin/roles`, {
    method: 'POST',
    body: JSON.stringify(body),
  });

/** GET /admin/users */
export const listUsers = () =>
  request<{ users: AdminUser[] }>(`${BASE}/admin/users`);

/** POST /admin/users */
export const createUser = (body: AdminUser) =>
  request<{ ok: boolean }>(`${BASE}/admin/users`, {
    method: 'POST',
    body: JSON.stringify(body),
  });

/** GET /admin/users/{id}/roles */
export const listUserRoles = (userId: string) =>
  request<{ user_id: string; roles: string[] }>(
    `${BASE}/admin/users/${encodeURIComponent(userId)}/roles`,
  );

/** POST /admin/users/{id}/roles */
export const bindUserRole = (userId: string, roleId: string) =>
  request<{ ok: boolean }>(
    `${BASE}/admin/users/${encodeURIComponent(userId)}/roles`,
    { method: 'POST', body: JSON.stringify({ role_id: roleId }) },
  );

/** GET /admin/deny_rules?db_id=&role_id= */
export const listDenyRules = (params?: { db_id?: string; role_id?: string }) => {
  const qs = new URLSearchParams();
  if (params?.db_id) qs.set('db_id', params.db_id);
  if (params?.role_id) qs.set('role_id', params.role_id);
  const q = qs.toString();
  return request<{ rules: DenyRule[] }>(
    `${BASE}/admin/deny_rules${q ? '?' + q : ''}`,
  );
};

/** POST /admin/deny_rules */
export const addDenyRule = (body: Omit<DenyRule, 'id'>) =>
  request<{ ok: boolean; id: number }>(`${BASE}/admin/deny_rules`, {
    method: 'POST',
    body: JSON.stringify(body),
  });

/** DELETE /admin/deny_rules/{id} */
export const deleteDenyRule = (id: number) =>
  request<{ ok: boolean }>(`${BASE}/admin/deny_rules/${id}`, {
    method: 'DELETE',
  });

/** GET /admin/permissions?user_id=&db_id= */
export const getEffectivePermissions = (userId: string, dbId: string) =>
  request<{ user_id: string; db_id: string; deny_rules: EffectivePermission[] }>(
    `${BASE}/admin/permissions?user_id=${encodeURIComponent(userId)}&db_id=${encodeURIComponent(dbId)}`,
  );

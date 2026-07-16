import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as admin from './admin';

beforeEach(() => {
  vi.restoreAllMocks();
});

function mockFetch(data: unknown, ok = true, status = 200) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok,
      status,
      text: async () => JSON.stringify(data),
    })) as unknown as typeof fetch,
  );
}

describe('admin API client', () => {
  it('listRoles GET /admin/roles', async () => {
    mockFetch({ roles: [{ role_id: 'staff', name: '员工' }] });
    const r = await admin.listRoles();
    expect(r.roles[0].role_id).toBe('staff');
    expect(fetch).toHaveBeenCalledWith('/api/v1/admin/roles', expect.anything());
  });

  it('createRole POST body', async () => {
    mockFetch({ ok: true });
    await admin.createRole({ role_id: 'mgr', name: '管理者' });
    const call = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(call[1].method).toBe('POST');
    expect(JSON.parse(call[1].body)).toEqual({ role_id: 'mgr', name: '管理者' });
  });

  it('addDenyRule returns id', async () => {
    mockFetch({ ok: true, id: 7 });
    const r = await admin.addDenyRule({
      db_id: 'd1',
      role_id: 'staff',
      table_pattern: 't',
      column_pattern: null,
      reason: null,
    });
    expect(r.id).toBe(7);
  });

  it('deleteDenyRule uses DELETE', async () => {
    mockFetch({ ok: true });
    await admin.deleteDenyRule(5);
    const call = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(call[0]).toBe('/api/v1/admin/deny_rules/5');
    expect(call[1].method).toBe('DELETE');
  });

  it('listDenyRules builds query string', async () => {
    mockFetch({ rules: [] });
    await admin.listDenyRules({ db_id: 'd1', role_id: 'staff' });
    const call = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(call[0]).toBe('/api/v1/admin/deny_rules?db_id=d1&role_id=staff');
  });

  it('bindUserRole POST to user path', async () => {
    mockFetch({ ok: true });
    await admin.bindUserRole('u1', 'staff');
    const call = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(call[0]).toBe('/api/v1/admin/users/u1/roles');
    expect(JSON.parse(call[1].body)).toEqual({ role_id: 'staff' });
  });

  it('getEffectivePermissions builds query', async () => {
    mockFetch({ user_id: 'u1', db_id: 'd1', deny_rules: [] });
    await admin.getEffectivePermissions('u1', 'd1');
    const call = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(call[0]).toContain('user_id=u1');
    expect(call[0]).toContain('db_id=d1');
  });

  it('throws on !ok with error message', async () => {
    mockFetch({ error: 'bad request' }, false);
    await expect(admin.listRoles()).rejects.toThrow('bad request');
  });

  it('throws HTTP status when no error field', async () => {
    mockFetch({}, false, 500);
    await expect(admin.listRoles()).rejects.toThrow('HTTP 500');
  });
});

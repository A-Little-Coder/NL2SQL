"""admin API 单测（task 7.4）

用 TestClient + dependency_overrides 注入临时 PolicyStore，避免加载 BGE。
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.deps import get_policy_store
from src.api.routes import admin as admin_router
from src.permission.init_auth import init_db
from src.permission.policy_store import PolicyStore


@pytest.fixture
def client_store(tmp_path):
    db = tmp_path / "auth.db"
    init_db(str(db))
    ps = PolicyStore(str(db))
    app = FastAPI()
    app.include_router(admin_router.router, prefix="/api/v1")
    app.dependency_overrides[get_policy_store] = lambda: ps
    return TestClient(app), ps


def test_crud_full_flow(client_store):
    """角色->员工->绑定->黑名单->列表->有效权限->删除 全流程"""
    client, _ = client_store

    assert client.post("/api/v1/admin/roles", json={"role_id": "staff", "name": "普通员工"}).json()["ok"]
    assert client.post("/api/v1/admin/users", json={"user_id": "u1", "name": "Alice", "dept": "销售"}).json()["ok"]
    assert client.post("/api/v1/admin/users/u1/roles", json={"role_id": "staff"}).json()["ok"]

    r = client.post("/api/v1/admin/deny_rules", json={
        "db_id": "db1", "role_id": "staff", "table_pattern": "employees",
        "column_pattern": "salary", "reason": "禁薪资",
    })
    rule_id = r.json()["id"]

    r = client.get("/api/v1/admin/deny_rules", params={"db_id": "db1"})
    assert len(r.json()["rules"]) == 1

    r = client.get("/api/v1/admin/permissions", params={"user_id": "u1", "db_id": "db1"})
    assert len(r.json()["deny_rules"]) == 1
    assert r.json()["deny_rules"][0]["column_pattern"] == "salary"

    assert client.delete(f"/api/v1/admin/deny_rules/{rule_id}").json()["ok"]
    assert len(client.get("/api/v1/admin/deny_rules", params={"db_id": "db1"}).json()["rules"]) == 0


def test_list_roles_and_users(client_store):
    client, ps = client_store
    ps.add_role("admin", "管理员")
    ps.add_user("u2", "Bob")
    roles = client.get("/api/v1/admin/roles").json()["roles"]
    assert any(x["role_id"] == "admin" for x in roles)
    users = client.get("/api/v1/admin/users").json()["users"]
    assert any(x["user_id"] == "u2" for x in users)


def test_user_roles_binding(client_store):
    client, _ = client_store
    client.post("/api/v1/admin/roles", json={"role_id": "mgr", "name": "管理者"})
    client.post("/api/v1/admin/users", json={"user_id": "u3", "name": "Carol"})
    client.post("/api/v1/admin/users/u3/roles", json={"role_id": "mgr"})
    r = client.get("/api/v1/admin/users/u3/roles").json()
    assert r["roles"] == ["mgr"]


def test_table_level_deny_rule(client_store):
    """column_pattern 为 null 表示整表禁"""
    client, _ = client_store
    client.post("/api/v1/admin/roles", json={"role_id": "staff", "name": "s"})
    client.post("/api/v1/admin/users", json={"user_id": "u4", "name": "D"})
    client.post("/api/v1/admin/users/u4/roles", json={"role_id": "staff"})
    client.post("/api/v1/admin/deny_rules", json={
        "db_id": "db1", "role_id": "staff", "table_pattern": "audit_log", "column_pattern": None,
    })
    r = client.get("/api/v1/admin/permissions", params={"user_id": "u4", "db_id": "db1"}).json()
    assert r["deny_rules"][0]["column_pattern"] is None
    assert r["deny_rules"][0]["table_pattern"] == "audit_log"

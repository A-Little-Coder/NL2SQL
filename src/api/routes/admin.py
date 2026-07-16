"""权限管理 REST API（table-field-acl）

管理后台 CRUD：角色 / 员工 / 角色绑定 / 黑名单规则 / 有效权限查询。
PolicyStore 通过依赖注入（便于测试 override）。
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from src.api.deps import get_policy_store
from src.permission.policy_store import PolicyStore

router = APIRouter()


# ── Pydantic 模型 ──────────────────────────────────────────


class RoleIn(BaseModel):
    role_id: str
    name: str


class UserIn(BaseModel):
    user_id: str
    name: str
    dept: Optional[str] = None


class UserRoleIn(BaseModel):
    role_id: str


class DenyRuleIn(BaseModel):
    db_id: str
    role_id: str
    table_pattern: str
    column_pattern: Optional[str] = None  # None = 整表禁
    reason: Optional[str] = None


# ── 角色 ───────────────────────────────────────────────────


@router.post("/admin/roles")
def create_role(body: RoleIn, ps: PolicyStore = Depends(get_policy_store)):
    ps.add_role(body.role_id, body.name)
    return {"ok": True}


@router.get("/admin/roles")
def list_roles(ps: PolicyStore = Depends(get_policy_store)):
    return {"roles": ps.list_roles()}


# ── 员工 ───────────────────────────────────────────────────


@router.post("/admin/users")
def create_user(body: UserIn, ps: PolicyStore = Depends(get_policy_store)):
    ps.add_user(body.user_id, body.name, body.dept)
    return {"ok": True}


@router.get("/admin/users")
def list_users(ps: PolicyStore = Depends(get_policy_store)):
    return {"users": ps.list_users()}


@router.post("/admin/users/{user_id}/roles")
def bind_user_role(user_id: str, body: UserRoleIn, ps: PolicyStore = Depends(get_policy_store)):
    ps.bind_user_role(user_id, body.role_id)
    return {"ok": True}


@router.get("/admin/users/{user_id}/roles")
def list_user_roles(user_id: str, ps: PolicyStore = Depends(get_policy_store)):
    return {"user_id": user_id, "roles": ps.list_user_roles(user_id)}


# ── 黑名单规则 ─────────────────────────────────────────────


@router.post("/admin/deny_rules")
def add_deny_rule(body: DenyRuleIn, ps: PolicyStore = Depends(get_policy_store)):
    rule_id = ps.add_deny_rule(
        body.db_id, body.role_id, body.table_pattern, body.column_pattern, body.reason
    )
    return {"ok": True, "id": rule_id}


@router.get("/admin/deny_rules")
def list_deny_rules(
    db_id: Optional[str] = Query(None),
    role_id: Optional[str] = Query(None),
    ps: PolicyStore = Depends(get_policy_store),
):
    return {"rules": ps.list_deny_rules(db_id=db_id, role_id=role_id)}


@router.delete("/admin/deny_rules/{rule_id}")
def delete_deny_rule(rule_id: int, ps: PolicyStore = Depends(get_policy_store)):
    ps.delete_deny_rule(rule_id)
    return {"ok": True}


# ── 有效权限查询 ───────────────────────────────────────────


@router.get("/admin/permissions")
def get_effective_permissions(
    user_id: str = Query(...),
    db_id: str = Query(...),
    ps: PolicyStore = Depends(get_policy_store),
):
    """查询某用户在某库的有效黑名单（多角色并集）"""
    rules = ps.get_effective_deny(db_id, user_id)
    return {
        "user_id": user_id,
        "db_id": db_id,
        "deny_rules": [
            {
                "table_pattern": r.table_pattern,
                "column_pattern": r.column_pattern,
                "reason": r.reason,
            }
            for r in rules
        ],
    }

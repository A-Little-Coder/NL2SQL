"""PolicyStore 单测（task 3.3）

覆盖：多角色并集、默认放行、字段禁、表级禁、通配表、denied_columns（表级/部分）、大小写无关。
"""

import sqlite3

import pytest

from src.permission.init_auth import SCHEMA, init_db
from src.permission.policy_store import PolicyStore


@pytest.fixture
def store(tmp_path) -> PolicyStore:
    """临时 auth.db + 测试数据"""
    db = tmp_path / "auth.db"
    init_db(str(db))
    with sqlite3.connect(str(db)) as conn:
        conn.executemany(
            "INSERT INTO roles(role_id,name) VALUES(?,?)",
            [("staff", "普通员工"), ("manager", "管理者")],
        )
        conn.executemany(
            "INSERT INTO users(user_id,name) VALUES(?,?)",
            [("u1", "Alice"), ("u2", "Bob")],
        )
        conn.executemany(
            "INSERT INTO user_roles(user_id,role_id) VALUES(?,?)",
            [("u1", "staff"), ("u1", "manager"), ("u2", "staff")],
        )
        conn.executemany(
            "INSERT INTO deny_rules(db_id,role_id,table_pattern,column_pattern,reason) "
            "VALUES(?,?,?,?,?)",
            [
                ("db1", "staff", "employees", "salary", "禁薪资"),
                ("db1", "staff", "employees", "phone", "禁手机"),
                ("db1", "manager", "employees", "phone", "禁手机"),
                ("db1", "staff", "audit_log", None, "整表禁"),
                ("db1", "staff", "*", "secret", "通配表"),
            ],
        )
        conn.commit()
    return PolicyStore(str(db))


def test_multi_role_union(store):
    """u1 挂 staff+manager，有效黑名单为并集，含 salary(staff) 与 phone(staff+manager)"""
    rules = store.get_effective_deny("db1", "u1")
    pairs = {(r.table_pattern, r.column_pattern) for r in rules}
    assert ("employees", "salary") in pairs
    assert ("employees", "phone") in pairs


def test_default_allow(store):
    """未在黑名单的字段默认放行"""
    rules = store.get_effective_deny("db1", "u2")
    assert not store.is_denied(rules, "employees", "name")
    assert not store.is_denied(rules, "orders", "amount")


def test_field_denied(store):
    """staff 角色的 salary/phone 被禁"""
    rules = store.get_effective_deny("db1", "u2")
    assert store.is_denied(rules, "employees", "salary")
    assert store.is_denied(rules, "employees", "phone")


def test_table_level_denied(store):
    """audit_log 整表禁 -> 任意列被禁，is_table_denied 为真"""
    rules = store.get_effective_deny("db1", "u2")
    assert store.is_denied(rules, "audit_log", "anything")
    assert store.is_table_denied(rules, "audit_log")
    assert not store.is_table_denied(rules, "employees")


def test_wildcard_table_pattern(store):
    """*.secret 通配表 -> 任意表的 secret 列被禁"""
    rules = store.get_effective_deny("db1", "u2")
    assert store.is_denied(rules, "orders", "secret")
    assert store.is_denied(rules, "anytable", "secret")
    assert not store.is_denied(rules, "orders", "amount")


def test_denied_columns_table_level(store):
    """表级禁 -> denied_columns 返回全部列"""
    rules = store.get_effective_deny("db1", "u2")
    denied = store.denied_columns(rules, "audit_log", ["a", "b", "c"])
    assert denied == {"a", "b", "c"}


def test_denied_columns_partial(store):
    """字段级禁 -> denied_columns 只返回无权限列"""
    rules = store.get_effective_deny("db1", "u2")
    denied = store.denied_columns(
        rules, "employees", ["name", "salary", "phone", "dept"]
    )
    assert denied == {"salary", "phone"}


def test_case_insensitive(store):
    """匹配忽略大小写"""
    rules = store.get_effective_deny("db1", "u2")
    assert store.is_denied(rules, "Employees", "SALARY")
    assert store.is_denied(rules, "AUDIT_LOG", "x")


def test_no_role_no_rules(store):
    """无角色的用户有效黑名单为空，全放行"""
    rules = store.get_effective_deny("db1", "nobody")
    assert rules == []
    assert not store.is_denied(rules, "employees", "salary")

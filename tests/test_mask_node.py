"""脱敏节点单测（task 5.5）：普通/聚合/flag/无规则/cache 路径"""

import sqlite3

import pytest

from src.permission.init_auth import init_db
from src.permission.mask_node import make_mask_node
from src.permission.policy_store import PolicyStore


@pytest.fixture
def store(tmp_path) -> PolicyStore:
    db = tmp_path / "auth.db"
    init_db(str(db))
    with sqlite3.connect(str(db)) as conn:
        conn.executemany("INSERT INTO roles VALUES(?,?)", [("staff", "s")])
        conn.executemany("INSERT INTO users(user_id, name) VALUES(?,?)", [("u1", "A")])
        conn.executemany("INSERT INTO user_roles VALUES(?,?)", [("u1", "staff")])
        conn.executemany(
            "INSERT INTO deny_rules(db_id,role_id,table_pattern,column_pattern,reason) "
            "VALUES(?,?,?,?,?)",
            [("db1", "staff", "employees", "salary", "禁薪资")],
        )
        conn.commit()
    return PolicyStore(str(db))


def test_mask_plain(store, monkeypatch):
    monkeypatch.setenv("TABLE_FIELD_ACL_ENABLED", "true")
    node = make_mask_node(store)
    out = node({
        "final_sql": "SELECT name, salary FROM employees",
        "final_result": [{"name": "Alice", "salary": 8000}, {"name": "Bob", "salary": 9000}],
        "user_id": "u1", "database_filter": "db1", "query_id": "q1", "trace_log": [],
    })
    assert out["final_result"][0]["salary"] == "***"
    assert out["final_result"][0]["name"] == "Alice"
    assert out["final_result"][1]["salary"] == "***"


def test_mask_aggregate(store, monkeypatch):
    monkeypatch.setenv("TABLE_FIELD_ACL_ENABLED", "true")
    node = make_mask_node(store)
    out = node({
        "final_sql": "SELECT dept, AVG(salary) FROM employees GROUP BY dept",
        "final_result": [{"dept": "A", "AVG(salary)": 8500}],
        "user_id": "u1", "database_filter": "db1", "query_id": "q1", "trace_log": [],
    })
    vals = out["final_result"][0]
    assert vals["dept"] == "A"
    assert "***" in vals.values()  # AVG(salary) 列脱敏


def test_mask_flag_off(store, monkeypatch):
    monkeypatch.setenv("TABLE_FIELD_ACL_ENABLED", "false")
    node = make_mask_node(store)
    out = node({
        "final_sql": "SELECT salary FROM employees",
        "final_result": [{"salary": 1}],
        "user_id": "u1", "database_filter": "db1",
    })
    assert out == {}


def test_mask_no_rules(store, monkeypatch):
    monkeypatch.setenv("TABLE_FIELD_ACL_ENABLED", "true")
    node = make_mask_node(store)
    # nobody 无角色 -> 无黑名单 -> 不脱敏
    out = node({
        "final_sql": "SELECT salary FROM employees",
        "final_result": [{"salary": 1}],
        "user_id": "nobody", "database_filter": "db1",
    })
    assert out == {}


def test_mask_cache_path_same_logic(store, monkeypatch):
    """cache 路径同样过脱敏节点（final_sql 来自 cached_sql，逻辑一致）"""
    monkeypatch.setenv("TABLE_FIELD_ACL_ENABLED", "true")
    node = make_mask_node(store)
    out = node({
        "final_sql": "SELECT salary FROM employees WHERE dept='A'",
        "final_result": [{"salary": 8000}],
        "user_id": "u1", "database_filter": "db1", "query_id": "q1", "trace_log": [],
    })
    assert out["final_result"][0]["salary"] == "***"


def test_mask_parse_fallback(store, monkeypatch):
    """SQL 解析失败时兜底脱敏（5.4）：列名命中黑名单字段名即脱敏"""
    monkeypatch.setenv("TABLE_FIELD_ACL_ENABLED", "true")
    node = make_mask_node(store)
    out = node({
        "final_sql": "this is not valid sql !!!",
        "final_result": [{"name": "Alice", "salary": 8000}],
        "user_id": "u1", "database_filter": "db1", "query_id": "q1", "trace_log": [],
    })
    assert out["final_result"][0]["salary"] == "***"
    assert out["final_result"][0]["name"] == "Alice"

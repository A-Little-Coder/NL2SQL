"""权限节点单测（task 4.9 / 11.4）：关键词级判断

覆盖：
- flag 关闭/无规则直通
- 部分无权剔除、表级禁整表移除、全有权直通
- 任一关键词全无权即反问（核心语义，task 11.4）
- SS 后无召回字段的关键词跳过（不触发反问）
- mask 分支：keep 字段保留、prune 字段剔除
- reject 分支：拒答
- _analyze 纯函数：多关键词 keep 优先于 prune
"""

import sqlite3

import pytest

from src.permission.init_auth import init_db
from src.permission.permission_node import _analyze, make_permission_node
from src.permission.policy_store import PolicyStore
from src.schema_selection.schema_selector import MSchemaColumn, MSchemaTable


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
            [
                ("db1", "staff", "employees", "salary", "禁薪资"),
                ("db1", "staff", "audit_log", None, "整表禁"),
            ],
        )
        conn.commit()
    return PolicyStore(str(db))


class FakeCtx:
    def __init__(self, kcm):
        self.keyword_columns_map = kcm


def _schema(tables):
    return [
        MSchemaTable(name=t, columns=[MSchemaColumn(name=c, data_type="text") for c in cols])
        for t, cols in tables
    ]


def test_flag_off_passthrough(store, monkeypatch):
    monkeypatch.setenv("TABLE_FIELD_ACL_ENABLED", "false")
    node = make_permission_node(store)
    out = node({
        "selected_schema": _schema([("employees", ["name", "salary"])]),
        "retrieved_context": FakeCtx({}),
        "user_id": "u1", "database_filter": "db1",
    })
    assert out == {}


def test_no_rules_passthrough(store, monkeypatch):
    monkeypatch.setenv("TABLE_FIELD_ACL_ENABLED", "true")
    node = make_permission_node(store)
    out = node({
        "selected_schema": _schema([("employees", ["name", "salary"])]),
        "retrieved_context": FakeCtx({"姓名": ["employees.name"]}),
        "user_id": "nobody", "database_filter": "db1",
    })
    assert out == {}


def test_partial_prune(store, monkeypatch):
    """部分无权：薪资关键词召回 salary(无权)+base_salary(有权) -> 剔除 salary"""
    monkeypatch.setenv("TABLE_FIELD_ACL_ENABLED", "true")
    node = make_permission_node(store)
    out = node({
        "selected_schema": _schema([("employees", ["name", "salary", "base_salary"])]),
        "retrieved_context": FakeCtx({"薪资": ["employees.salary", "employees.base_salary"]}),
        "user_id": "u1", "database_filter": "db1", "query_id": "q", "trace_log": [],
    })
    cols = [c.name for t in out["selected_schema"] for c in t.columns]
    assert "salary" not in cols
    assert "base_salary" in cols
    assert "name" in cols


def test_table_level_removed(store, monkeypatch):
    """表级禁（audit_log）-> 整表移除"""
    monkeypatch.setenv("TABLE_FIELD_ACL_ENABLED", "true")
    node = make_permission_node(store)
    out = node({
        "selected_schema": _schema([("employees", ["name"]), ("audit_log", ["a", "b"])]),
        "retrieved_context": FakeCtx({"日志": ["audit_log.a", "employees.name"]}),
        "user_id": "u1", "database_filter": "db1", "query_id": "q", "trace_log": [],
    })
    table_names = [t.name for t in out["selected_schema"]]
    assert "audit_log" not in table_names
    assert "employees" in table_names


def test_all_allowed_passthrough(store, monkeypatch):
    """全有权：不剔除、不反问，直通（不改 schema）"""
    monkeypatch.setenv("TABLE_FIELD_ACL_ENABLED", "true")
    node = make_permission_node(store)
    out = node({
        "selected_schema": _schema([("employees", ["name", "dept"])]),
        "retrieved_context": FakeCtx({"姓名": ["employees.name"]}),
        "user_id": "u1", "database_filter": "db1", "query_id": "q", "trace_log": [],
    })
    assert out == {}


# ---- task 11.4：任一关键词全无权即反问（核心语义）----

def test_analyze_any_keyword_full_deny(store, monkeypatch):
    """核心语义：姓名有权 + 薪资全无权 -> has_full_deny=True（任一关键词全无权即反问）"""
    monkeypatch.setenv("TABLE_FIELD_ACL_ENABLED", "true")
    rules = store.get_effective_deny("db1", "u1")
    schema = _schema([("employees", ["name", "salary"])])
    kcm = {"姓名": ["employees.name"], "薪资": ["employees.salary"]}
    analysis = _analyze(schema, kcm, rules)
    assert analysis["has_full_deny"] is True
    assert "employees.salary" in analysis["keep_fields"]
    assert analysis["prune_fields"] == []


def test_analyze_empty_keyword_skipped(store, monkeypatch):
    """SS 后无字段进入 schema 的关键词跳过，不触发全无权"""
    monkeypatch.setenv("TABLE_FIELD_ACL_ENABLED", "true")
    rules = store.get_effective_deny("db1", "u1")
    # 薪资关键词召回 salary（无权），但 SS 未选 salary -> S=∅ -> 跳过
    schema = _schema([("employees", ["name"])])
    kcm = {"薪资": ["employees.salary"]}
    analysis = _analyze(schema, kcm, rules)
    assert analysis["has_full_deny"] is False
    assert analysis["keep_fields"] == []
    assert analysis["prune_fields"] == []


def test_empty_keyword_skipped_no_interrupt(store, monkeypatch):
    """关键词召回字段均未进 schema -> 节点直通，不反问（interrupt 未被调用）"""
    monkeypatch.setenv("TABLE_FIELD_ACL_ENABLED", "true")
    node = make_permission_node(store)
    out = node({
        "selected_schema": _schema([("employees", ["name"])]),
        "retrieved_context": FakeCtx({"薪资": ["employees.salary"]}),
        "user_id": "u1", "database_filter": "db1", "query_id": "q", "trace_log": [],
    })
    assert out == {}  # 无全无权（薪资被跳过）、无剔除 -> 直通


def test_analyze_keep_wins_over_prune(store, monkeypatch):
    """同一字段被全无权组与部分无权组同时召回时，keep 优先（全无权语义优先）"""
    monkeypatch.setenv("TABLE_FIELD_ACL_ENABLED", "true")
    rules = store.get_effective_deny("db1", "u1")
    schema = _schema([("employees", ["name", "salary"])])
    # salary 被「薪资」（全无权）和「混合」（部分：salary 无权 + name 有权）同时召回
    kcm = {"薪资": ["employees.salary"], "混合": ["employees.salary", "employees.name"]}
    analysis = _analyze(schema, kcm, rules)
    assert analysis["has_full_deny"] is True
    assert "employees.salary" in analysis["keep_fields"]
    assert "employees.salary" not in analysis["prune_fields"]


def test_full_deny_mask_keeps_and_prunes(store, monkeypatch):
    """任一关键词全无权 + 选脱敏：keep 字段保留供脱敏，部分无权字段仍剔除"""
    monkeypatch.setenv("TABLE_FIELD_ACL_ENABLED", "true")
    import src.permission.permission_node as pn
    monkeypatch.setattr(pn, "interrupt", lambda payload: "mask")
    node = make_permission_node(store)
    # 薪资全无权（salary 无权 -> keep）；日志部分无权（audit_log.a 无权 -> prune，name 有权）
    out = node({
        "selected_schema": _schema([("employees", ["name", "salary"]), ("audit_log", ["a"])]),
        "retrieved_context": FakeCtx({
            "薪资": ["employees.salary"],
            "日志": ["audit_log.a", "employees.name"],
        }),
        "user_id": "u1", "database_filter": "db1", "query_id": "q", "trace_log": [],
    })
    cols = [c.name for t in out["selected_schema"] for c in t.columns]
    assert "salary" in cols        # keep：全无权，保留供脱敏
    assert "name" in cols          # 有权保留
    assert "a" not in cols         # prune：部分无权剔除
    table_names = [t.name for t in out["selected_schema"]]
    assert "audit_log" not in table_names  # a 被剔除后空表移除
    assert out["acl_removed_fields"] == ["audit_log.a"]


def test_full_deny_reject(store, monkeypatch):
    """任一关键词全无权 + 选放弃 -> 拒答"""
    monkeypatch.setenv("TABLE_FIELD_ACL_ENABLED", "true")
    import src.permission.permission_node as pn
    monkeypatch.setattr(pn, "interrupt", lambda payload: "reject")
    node = make_permission_node(store)
    out = node({
        "selected_schema": _schema([("employees", ["name", "salary"])]),
        "retrieved_context": FakeCtx({"姓名": ["employees.name"], "薪资": ["employees.salary"]}),
        "user_id": "u1", "database_filter": "db1", "query_id": "q", "trace_log": [],
    })
    assert "rejection_reason" in out
    assert "salary" in out["rejection_reason"]
    assert out["selected_schema"] == []


def test_full_deny_multi_intent_downgrade(store, monkeypatch):
    """多意图路径全无权 -> 直接拒答该子查询（不反问）"""
    monkeypatch.setenv("TABLE_FIELD_ACL_ENABLED", "true")
    node = make_permission_node(store)
    out = node({
        "selected_schema": _schema([("employees", ["name", "salary"])]),
        "retrieved_context": FakeCtx({"薪资": ["employees.salary"]}),
        "user_id": "u1", "database_filter": "db1", "query_id": "q", "trace_log": [],
        "_multi_intent": True,
    })
    assert "rejection_reason" in out
    assert out["selected_schema"] == []

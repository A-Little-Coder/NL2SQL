"""EventCacheStore 单测（change session-restore-event-cache）

覆盖：shard 分配、index 读写、事件流追加、result 截断、resume 跨阶段缓冲合并、
分页、读取、删除、用户隔离、clear_pending。
"""

import pytest

from src.memory.event_cache import EventCacheStore, RESULT_ROW_LIMIT, SHARD_SIZE


@pytest.fixture
def store(tmp_path):
    """用 tmp_path 隔离的 EventCacheStore"""
    return EventCacheStore(base_dir=str(tmp_path))


def test_register_session_assigns_shard(store):
    shard = store.register_session("u1", "s1")
    assert shard == "shard_0001"
    events = store.get_session_events("u1", "s1")
    assert events is not None
    assert events["has_events"] is False
    assert events["turns"] == []


def test_register_session_idempotent(store):
    s1 = store.register_session("u1", "s1")
    s2 = store.register_session("u1", "s1")
    assert s1 == s2


def test_shard_full_opens_new_shard(store):
    for i in range(SHARD_SIZE):
        store.register_session("u1", f"s{i}")
    # 第 21 个会话开新 shard
    shard = store.register_session("u1", "s20")
    assert shard == "shard_0002"


def test_store_turn_events_appends(store):
    store.register_session("u1", "s1")
    events = [{"type": "stage", "data": {"node": "ir", "status": "done"}}]
    store.store_turn_events("u1", "s1", events, is_pending=False, user_query="查销售额")
    data = store.get_session_events("u1", "s1")
    assert data["has_events"] is True
    assert len(data["turns"]) == 1
    assert data["turns"][0]["user_query"] == "查销售额"
    assert data["turns"][0]["events"] == events


def test_result_truncation(store):
    store.register_session("u1", "s1")
    rows = [{"c": i} for i in range(100)]
    events = [{"type": "result", "data": {"sql": "SELECT 1", "result": rows}}]
    store.store_turn_events("u1", "s1", events, is_pending=False)
    data = store.get_session_events("u1", "s1")
    result_evt = data["turns"][0]["events"][0]
    assert len(result_evt["data"]["result"]) == RESULT_ROW_LIMIT
    assert result_evt["data"]["__truncated__"] is True


def test_result_not_truncated_when_small(store):
    store.register_session("u1", "s1")
    rows = [{"c": i} for i in range(5)]
    events = [{"type": "result", "data": {"sql": "SELECT 1", "result": rows}}]
    store.store_turn_events("u1", "s1", events, is_pending=False)
    data = store.get_session_events("u1", "s1")
    result_evt = data["turns"][0]["events"][0]
    assert len(result_evt["data"]["result"]) == 5
    assert result_evt["data"]["__truncated__"] is False


def test_resume_pending_merge(store):
    """query 阶段暂存 pending + resume 完成合并为一个完整 turn（D5）"""
    store.register_session("u1", "s1")
    query_events = [{"type": "keywords", "data": {"groups": []}}]
    store.store_turn_events("u1", "s1", query_events, is_pending=True, user_query="原问题")
    resume_events = [{"type": "result", "data": {"sql": "SELECT 1", "result": []}}]
    store.store_turn_events("u1", "s1", resume_events, is_pending=False, user_query="")
    data = store.get_session_events("u1", "s1")
    assert len(data["turns"]) == 1
    types = [e["type"] for e in data["turns"][0]["events"]]
    assert "keywords" in types  # query 阶段事件保留
    assert "result" in types  # resume 阶段事件合并
    # user_query 取 query 阶段暂存的（resume 时 body.query 可空）
    assert data["turns"][0]["user_query"] == "原问题"


def test_clear_pending(store):
    """新查询开始清旧 pending，避免误合并到新 turn（D5）"""
    store.register_session("u1", "s1")
    store.store_turn_events("u1", "s1", [{"type": "keywords", "data": {}}], is_pending=True)
    store.clear_pending("u1", "s1")
    store.store_turn_events("u1", "s1", [{"type": "result", "data": {"sql": "x", "result": []}}], is_pending=False)
    data = store.get_session_events("u1", "s1")
    assert len(data["turns"]) == 1
    types = [e["type"] for e in data["turns"][0]["events"]]
    assert "keywords" not in types  # pending 已清，未误合并


def test_list_sessions_paged(store):
    for i in range(25):
        store.register_session("u1", f"s{i:02d}")
    page0 = store.list_sessions_paged("u1", page=0)
    assert page0["has_more"] is True
    assert len(page0["sessions"]) <= SHARD_SIZE
    # page0 全部属于同一 shard（最新 shard）
    page0_shards = {s["shard_id"] for s in page0["sessions"]}
    assert len(page0_shards) == 1
    page1 = store.list_sessions_paged("u1", page=1)
    assert len(page1["sessions"]) <= SHARD_SIZE
    # 越界页返回空
    page9 = store.list_sessions_paged("u1", page=9)
    assert page9["sessions"] == []
    assert page9["has_more"] is False


def test_delete_session(store):
    store.register_session("u1", "s1")
    assert store.delete_session("u1", "s1") is True
    assert store.get_session_events("u1", "s1") is None
    assert store.delete_session("u1", "s1") is False


def test_user_isolation(store):
    store.register_session("u1", "s1")
    # u2 查 u1 的会话 -> None
    assert store.get_session_events("u2", "s1") is None


def test_update_turn_meta(store):
    """turn done 后 index 的 turn_count / updated_at 更新"""
    store.register_session("u1", "s1")
    store.store_turn_events("u1", "s1", [{"type": "result", "data": {"sql": "x", "result": []}}], is_pending=False)
    page = store.list_sessions_paged("u1", page=0)
    assert page["sessions"][0]["turn_count"] == 1

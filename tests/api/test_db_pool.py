"""DbContextPool 单元测试（决策 49）

不依赖真实数据库 / BGE / LLM —— 用 mock 替代 _build。
"""

import threading
from unittest.mock import MagicMock

import pytest

from src.api.db_pool import DbContext, DbContextPool, Globals


def _make_mock_ctx(db_id: str) -> DbContext:
    """构造一个干净的 mock DbContext（close 不抛异常）"""
    connector = MagicMock()
    connector.disconnect = MagicMock()
    return DbContext(
        db_id=db_id,
        db_path=f"/fake/{db_id}.sqlite",
        connector=connector,
        lsh_indexer=None,
        retriever=MagicMock(),
        selector=MagicMock(),
        executor=MagicMock(),
        fix_loop=MagicMock(),
        graph=MagicMock(),
    )


def _make_pool(max_size: int = 2) -> DbContextPool:
    """构造一个不会真实构建 ctx 的池"""
    g = Globals(
        bge_vectorizer=None,
        vector_store=None,
        llm_client=MagicMock(),
        generator=MagicMock(),
        decider=MagicMock(),
        answerability_checker=MagicMock(),
        history_cache=MagicMock(),
        memory_updater=MagicMock(),
        data_dir="/fake/data",
        memory_dir="/fake/memory",
    )
    pool = DbContextPool(max_size=max_size, globals_=g)
    # 替换 _build 为 mock 工厂
    pool._build = _make_mock_ctx  # type: ignore
    return pool


# ── LRU 顺序 ─────────────────────────────────────────────────

def test_acquire_caches_ctx():
    pool = _make_pool(max_size=2)
    a1 = pool.acquire("A")
    pool.release("A")
    a2 = pool.acquire("A")
    pool.release("A")
    assert a1 is a2, "同 db_id 第二次 acquire 应命中缓存"


def test_lru_eviction_when_full():
    pool = _make_pool(max_size=2)
    pool.acquire("A"); pool.release("A")
    pool.acquire("B"); pool.release("B")
    # 此时缓存 [A, B]，A 最久未用
    pool.acquire("C"); pool.release("C")
    cached_ids = [item["db_id"] for item in pool.stats()["cached"]]
    assert "A" not in cached_ids, "最久未用的 A 应被淘汰"
    assert set(cached_ids) == {"B", "C"}


def test_move_to_end_on_hit():
    pool = _make_pool(max_size=2)
    pool.acquire("A"); pool.release("A")
    pool.acquire("B"); pool.release("B")
    # 访问 A → A 移到末尾，B 成为最久未用
    pool.acquire("A"); pool.release("A")
    pool.acquire("C"); pool.release("C")
    cached_ids = [item["db_id"] for item in pool.stats()["cached"]]
    assert "B" not in cached_ids, "访问 A 后 B 成为最久未用，应被淘汰"
    assert set(cached_ids) == {"A", "C"}


# ── 引用计数 ──────────────────────────────────────────────────

def test_refcount_inc_dec():
    pool = _make_pool(max_size=2)
    pool.acquire("A")
    pool.acquire("A")
    assert pool.peek("A").refcount == 2
    pool.release("A")
    assert pool.peek("A").refcount == 1
    pool.release("A")
    assert pool.peek("A").refcount == 0


def test_busy_ctx_not_evicted():
    """池满 + 所有 ctx 都在用 → 允许短暂超 max，不淘汰"""
    pool = _make_pool(max_size=2)
    pool.acquire("A")  # refcount=1
    pool.acquire("B")  # refcount=1
    # 此时 [A,B] 都在用，加 C 应跳过淘汰
    pool.acquire("C")
    cached_ids = [item["db_id"] for item in pool.stats()["cached"]]
    assert set(cached_ids) == {"A", "B", "C"}, "全部在用时应允许短暂超 max"
    pool.release("A"); pool.release("B"); pool.release("C")


def test_evict_skips_busy_finds_idle():
    """池满，A 在用 / B 空闲 → 应淘汰 B 而不是 A"""
    pool = _make_pool(max_size=2)
    pool.acquire("A")              # A.refcount=1
    pool.acquire("B"); pool.release("B")   # B.refcount=0
    pool.acquire("C"); pool.release("C")
    cached_ids = [item["db_id"] for item in pool.stats()["cached"]]
    assert "A" in cached_ids, "在用的 A 不能被淘汰"
    assert "B" not in cached_ids, "空闲的 B 应被淘汰"
    pool.release("A")


# ── close_all ────────────────────────────────────────────────

def test_close_all_disconnects_each_ctx():
    pool = _make_pool(max_size=2)
    pool.acquire("A"); pool.release("A")
    pool.acquire("B"); pool.release("B")
    ctx_a = pool.peek("A")
    ctx_b = pool.peek("B")
    pool.close_all()
    ctx_a.connector.disconnect.assert_called()
    ctx_b.connector.disconnect.assert_called()
    assert pool.stats()["size"] == 0


# ── 并发 ─────────────────────────────────────────────────────

def test_concurrent_acquire_release_thread_safety():
    pool = _make_pool(max_size=4)

    def worker(db_id, n_iter):
        for _ in range(n_iter):
            pool.acquire(db_id)
            pool.release(db_id)

    threads = [
        threading.Thread(target=worker, args=(f"db_{i}", 50))
        for i in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 所有 ctx 的 refcount 应回到 0
    for item in pool.stats()["cached"]:
        assert item["refcount"] == 0, f"{item['db_id']} refcount 不归零"


def test_release_unknown_db_id_is_noop():
    """release 未 acquire 过的 db_id 不应抛异常"""
    pool = _make_pool(max_size=2)
    pool.release("never_acquired")  # 不应抛


# ── _build 端到端注入（harden-history-cache 回归）──────────────

def test_build_passes_llm_client_to_main_graph(monkeypatch):
    """回归：_build 必须把 g.llm_client 传给 build_main_graph

    历史 bug：db_pool.py 调 build_main_graph 时漏传 llm_client，导致主图 value_rewrite
    节点恒走 `if llm_client is None` 降级分支（不 emit 事件、adjusted_cached_sql 直接
    回落原 cached_sql），值参数改写从未生效。该路径被端到端服务验证发现——单元测试
    因 _make_pool 把 pool._build 整体替换为 mock 而绕过，形成集成盲区。

    本测试 mock 掉 _build 内所有重组件构造，专测 build_main_graph 的 llm_client 注入。
    """
    from src.api import db_pool

    # mock _build 内全部重组件，仅保留对 build_main_graph 调用的观测
    monkeypatch.setattr(db_pool, "_find_db_path",
                        lambda data_dir, db_id: f"/fake/{db_id}.sqlite")
    monkeypatch.setattr(db_pool, "DatabaseConnector", MagicMock())
    monkeypatch.setattr(db_pool, "_prepare_lsh_indexer", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(db_pool, "InformationRetrieval", MagicMock())
    monkeypatch.setattr(db_pool, "SchemaSelector", MagicMock())
    monkeypatch.setattr(db_pool, "SQLExecutor", MagicMock())
    monkeypatch.setattr(db_pool, "SQLFixLoop", MagicMock())
    # build_single_query_graph 是 _build 内函数级 import，patch 源模块
    monkeypatch.setattr("src.graph.single_query_graph.build_single_query_graph",
                        MagicMock(return_value=MagicMock()))

    captured = {}

    def fake_build_main_graph(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(db_pool, "build_main_graph", fake_build_main_graph)

    g = Globals(
        bge_vectorizer=None,
        vector_store=None,
        llm_client=MagicMock(name="the_llm"),
        generator=MagicMock(),
        decider=MagicMock(),
        answerability_checker=MagicMock(),
        history_cache=MagicMock(),
        memory_updater=MagicMock(),
        data_dir="/fake/data",
        memory_dir="/fake/memory",
    )
    pool = DbContextPool(max_size=2, globals_=g)

    pool._build("california_schools")

    # 核心断言：llm_client 必须被注入主图（value_rewrite 节点依赖它）
    assert "llm_client" in captured, "build_main_graph 必须收到 llm_client 参数"
    assert captured["llm_client"] is g.llm_client, \
        "llm_client 必须是 g.llm_client 同一实例，否则 value_rewrite 节点会降级"

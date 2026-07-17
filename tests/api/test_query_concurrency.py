"""查询并发闸测试（change multi-session-concurrency，design D1/D6）

覆盖：
- ConcurrencyGate 单元：计数 / FIFO / 超时不泄漏 / stats（任务 3.1/3.2/3.3-gate/3.5-gate）
- endpoint 集成：未排队不推 queued / 排队超时返回 error(queue_timeout)+done / /health 计数（3.3/3.4/3.5）
- 共享状态并发：event_cache 同用户并发不丢 turn / session_manager 并发不崩（3.6）

注：asyncio.Condition 是 loop-bound，所有 async 场景在单个 asyncio.run 内完成，
用 httpx.AsyncClient + ASGITransport 共享同一 loop 做并发请求（无需 pytest-asyncio）。
"""
import asyncio
import json
import threading
import time
from unittest.mock import MagicMock

import httpx
import pytest

from src.api.concurrency_gate import ConcurrencyGate


# ════════════════════════════════════════════════════════════
# ConcurrencyGate 单元
# ════════════════════════════════════════════════════════════

def test_gate_counts_and_try_acquire():
    """3.1：max=N 时恰好 N 个在飞，第 N+1 个 try_acquire 失败。"""
    async def run():
        g = ConcurrencyGate(max_concurrency=2, queue_timeout=5)
        assert await g.try_acquire()            # in_flight=1
        assert await g.try_acquire()            # in_flight=2
        assert not await g.try_acquire()        # 满
        s = g.stats()
        assert s["in_flight"] == 2 and s["waiting"] == 0 and s["max"] == 2
        await g.release()
        assert g.stats()["in_flight"] == 1
    asyncio.run(run())


def test_gate_fifo():
    """3.2：超额请求按到达顺序（FIFO）获取槽位。"""
    async def run():
        g = ConcurrencyGate(max_concurrency=1, queue_timeout=5)
        await g.try_acquire()  # 占满唯一槽位
        order = []

        async def w(name):
            if await g.acquire(timeout=5):
                order.append(name)

        tasks = [asyncio.ensure_future(w(n)) for n in ["a", "b", "c"]]
        await asyncio.sleep(0.1)
        assert g.stats()["waiting"] == 3
        for _ in range(3):
            await g.release()
            await asyncio.sleep(0.05)
        await asyncio.gather(*tasks)
        assert order == ["a", "b", "c"]
    asyncio.run(run())


def test_gate_timeout_no_leak():
    """3.5(gate)：排队超时返回 False 且不泄漏槽位（in_flight 不变）。"""
    async def run():
        g = ConcurrencyGate(max_concurrency=1, queue_timeout=5)
        await g.try_acquire()  # 占满
        t0 = time.time()
        got = await g.acquire(timeout=0.2)
        assert got is False
        assert 0.15 < time.time() - t0 < 0.6
        s = g.stats()
        assert s["in_flight"] == 1 and s["waiting"] == 0  # 超时未获槽位，不泄漏
    asyncio.run(run())


# ════════════════════════════════════════════════════════════
# endpoint 集成
# ════════════════════════════════════════════════════════════

class _FastGraph:
    """立即完成的图。"""
    def stream(self, initial_state, **kwargs):
        yield {"decision": {"final_sql": "SELECT 1", "final_result": [{"x": 1}]}}


class _BlockingGraph:
    """阻塞到 release 被 set 才完成，用于占住槽位。"""
    def __init__(self, release):
        self.release = release

    def stream(self, initial_state, **kwargs):
        self.release.wait(timeout=10)
        yield {"decision": {"final_sql": "SELECT 1", "final_result": [{"x": 1}]}}


@pytest.fixture
def gate_app(tmp_path, monkeypatch):
    """monkeypatch deps 装配轻量 app + max=1 / queue_timeout=0.4s 的并发闸。"""
    from src.api import deps
    from src.memory.session_manager import SessionManager

    sm = SessionManager(base_dir=str(tmp_path / "sessions"), max_cache_size=20)

    class _UM:
        def get_metric_definitions(self, min_confidence=0.7):
            return []

        def get_query_preferences(self):
            return {}

    monkeypatch.setattr(deps, "_session_manager", sm)
    monkeypatch.setattr(deps, "_event_cache", MagicMock())
    monkeypatch.setattr(
        deps, "_globals", MagicMock(data_dir=str(tmp_path), memory_dir=str(tmp_path / "memory"))
    )
    monkeypatch.setattr(deps, "_user_memory_cache", {})
    monkeypatch.setattr(deps, "get_user_memory", lambda user_id: _UM())

    gate = ConcurrencyGate(max_concurrency=1, queue_timeout=0.4)
    monkeypatch.setattr(deps, "_query_gate", gate)

    def _factory(graph_obj):
        class _Pool:
            def __init__(self):
                self.released = 0

            def acquire(self, db_id):
                ctx = MagicMock()
                ctx.graph = graph_obj
                ctx.fix_loop = None
                return ctx

            def release(self, db_id):
                self.released += 1

            def stats(self):
                return {"max": 2, "size": 0, "cached": []}

        pool = _Pool()
        monkeypatch.setattr(deps, "_db_pool", pool)
        return pool

    from src.api.app import app
    return app, gate, _factory


async def _sse_events(client, body):
    """收集一条 /query SSE 流的所有 data 事件为 dict 列表。"""
    evts = []
    async with client.stream("POST", "/api/v1/query", json=body) as resp:
        async for line in resp.aiter_lines():
            if line.startswith("data:"):
                try:
                    evts.append(json.loads(line[5:].strip()))
                except Exception:
                    pass
    return evts


def test_no_queued_when_immediate(gate_app):
    """3.4(负)：未达上限立即执行 -> 不推 queued，收 result+done。"""
    app, gate, factory = gate_app
    factory(_FastGraph())

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            evts = await _sse_events(c, {"query": "q", "session_id": "s1", "user_id": "u", "db_id": "any"})
        types = [e["type"] for e in evts]
        assert "queued" not in types
        assert "result" in types
        assert types[-1] == "done"

    asyncio.run(run())


def test_queued_then_timeout(gate_app):
    """3.4(正)+3.5：max=1，A 占槽（阻塞图），B 排队超时 -> queued + error(queue_timeout) + done，无 result。"""
    app, gate, factory = gate_app
    release = threading.Event()
    factory(_BlockingGraph(release))

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            # A 先发，占住唯一槽位（图阻塞）
            a_task = asyncio.ensure_future(
                _sse_events(c, {"query": "q", "session_id": "sA", "user_id": "u", "db_id": "any"})
            )
            await asyncio.sleep(0.2)  # 让 A 获取槽位并阻塞
            assert gate.stats()["in_flight"] == 1
            # B 排队 -> 超时
            b_evts = await _sse_events(c, {"query": "q", "session_id": "sB", "user_id": "u", "db_id": "any"})
            b_types = [e["type"] for e in b_evts]
            assert "queued" in b_types
            err = [e for e in b_evts if e["type"] == "error"]
            assert err and err[0]["data"].get("queue_timeout") is True
            assert "排队超时" in err[0]["data"].get("error", "")
            assert b_types[-1] == "done"
            assert "result" not in b_types
            # B 超时未获槽位 -> 闸不泄漏
            assert gate.stats()["in_flight"] == 1
            # 释放 A，A 正常完成
            release.set()
            await a_task

    asyncio.run(run())


def test_health_shows_concurrency(gate_app):
    """3.3：/health 暴露 in_flight/max；A 在飞时 in_flight=1，完成后=0。"""
    app, gate, factory = gate_app
    release = threading.Event()
    factory(_BlockingGraph(release))

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            a_task = asyncio.ensure_future(
                _sse_events(c, {"query": "q", "session_id": "sA", "user_id": "u", "db_id": "any"})
            )
            await asyncio.sleep(0.2)
            r = await c.get("/api/v1/health")
            conc = r.json()["concurrency"]
            assert conc["in_flight"] == 1 and conc["max"] == 1
            release.set()
            await a_task
            r2 = await c.get("/api/v1/health")
            assert r2.json()["concurrency"]["in_flight"] == 0

    asyncio.run(run())


# ════════════════════════════════════════════════════════════
# 共享状态并发（3.6）
# ════════════════════════════════════════════════════════════

def test_event_cache_concurrent_same_user_no_lost_update(tmp_path):
    """3.6：同用户并发 store_turn_events 不丢 turn（per-user RLock 生效）。"""
    from src.memory.event_cache import EventCacheStore

    store = EventCacheStore(base_dir=str(tmp_path / "ec"))
    store.register_session("u", "sess")

    barrier = threading.Barrier(5)
    errors = []

    def writer(i):
        try:
            barrier.wait()
            store.store_turn_events(
                "u", "sess",
                [{"type": "done", "data": {"i": i}}],
                is_pending=False, user_query=f"q{i}",
            )
        except Exception as e:
            errors.append(e)

    ts = [threading.Thread(target=writer, args=(i,)) for i in range(5)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors, errors
    data = store.get_session_events("u", "sess")
    assert len(data["turns"]) == 5, f"应 5 个 turn，实际 {len(data['turns'])}（丢更新！）"


def test_session_manager_concurrent_access_no_crash(tmp_path):
    """3.6：session_manager 并发 get_or_create+add_turn 不抛异常（_cache RLock 生效）。"""
    from src.memory.session_manager import SessionManager

    sm = SessionManager(base_dir=str(tmp_path / "sm"), max_cache_size=20)
    errors = []

    def worker(i):
        try:
            sid = f"sess{i}"
            m = sm.get_or_create_session(sid, "u")
            m.add_turn({"user_query": f"q{i}", "final_sql": "SELECT 1"})
        except Exception as e:
            errors.append(e)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors, errors

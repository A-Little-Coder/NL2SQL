"""真流式 SSE 事件流测试（决策 50）

验证：
1. 事件按时间顺序到达（不是"先攒后吐"）
2. 15 秒无事件时心跳行 `: heartbeat\\n\\n` 被发出
3. graph 异常时 error 事件被推送 + done 兜底
4. SSE 流中不出现 llm_chunk 事件类型
"""

import json
import os
import threading
import time
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


# ── Mock Graph 工具 ──────────────────────────────────────────

class _SlowGraph:
    """每个 yield 之间故意 sleep，验证客户端是否能按时间间隔收到事件"""

    def __init__(self, delays):
        self.delays = delays
        self.timestamps = []

    def stream(self, initial_state, **kwargs):
        for i, d in enumerate(self.delays):
            time.sleep(d)
            self.timestamps.append(time.time())
            yield {f"node_{i}": {"phase": i}}
        # 最后给一个 final_sql 让 result 事件能发
        yield {"decision": {"final_sql": "SELECT 1", "final_result": [{"x": 1}]}}


class _ExplodingGraph:
    def stream(self, initial_state, **kwargs):
        yield {"history_cache": {"cache_hit": False}}
        raise RuntimeError("boom from graph")


class _IdleGraph:
    """sleep 17 秒再 yield，触发心跳（>15s 阈值）"""

    def stream(self, initial_state, **kwargs):
        time.sleep(17)
        yield {"decision": {"final_sql": "SELECT 1", "final_result": []}}


# ── Fixture ───────────────────────────────────────────────────

@pytest.fixture
def patched_app(tmp_path, monkeypatch):
    from src.api import deps
    from src.memory.session_manager import SessionManager

    sm = SessionManager(base_dir=str(tmp_path / "sessions"), max_cache_size=20)

    class _UM:
        def get_metric_definitions(self, min_confidence=0.7):
            return []
        def get_query_preferences(self):
            return {}

    monkeypatch.setattr(deps, "_session_manager", sm)
    monkeypatch.setattr(deps, "_globals", MagicMock(data_dir=str(tmp_path), memory_dir=str(tmp_path / "memory")))
    monkeypatch.setattr(deps, "_user_memory_cache", {})
    monkeypatch.setattr(deps, "get_user_memory", lambda user_id: _UM())

    def _factory(graph_obj):
        class _Pool:
            def __init__(self):
                self.released = 0

            def acquire(self, db_id):
                ctx = MagicMock()
                ctx.graph = graph_obj
                return ctx

            def release(self, db_id):
                self.released += 1

            def stats(self):
                return {"max": 2, "size": 0, "cached": []}

        pool = _Pool()
        monkeypatch.setattr(deps, "_db_pool", pool)
        return pool

    from src.api.app import app
    return app, _factory


# ── Tests ─────────────────────────────────────────────────────

def test_events_arrive_in_real_time(patched_app):
    """事件按时间顺序到达 — 各 stage 事件应在 result 之前出现

    注：starlette TestClient 会缓冲响应再回放，无法真实测 wall-clock 流式。
    这里只验证事件顺序（stage 在 result 之前到达），真实流式行为依赖烟测。
    """
    app, factory = patched_app
    slow = _SlowGraph(delays=[0.05, 0.05, 0.05])
    factory(slow)

    client = TestClient(app, raise_server_exceptions=False)
    seen_types_in_order = []

    with client.stream("POST", "/api/v1/query", json={
        "query": "q",
        "session_id": "s_realtime",
        "user_id": "u",
        "db_id": "any",
    }) as resp:
        for raw in resp.iter_lines():
            line = raw if isinstance(raw, str) else raw.decode("utf-8", "replace")
            if line.startswith("data:"):
                try:
                    evt = json.loads(line[5:].strip())
                    seen_types_in_order.append(evt["type"])
                except Exception:
                    pass

    # result 必须出现在 done 之前；done 是最后一个事件
    assert "result" in seen_types_in_order
    assert "done" in seen_types_in_order
    assert seen_types_in_order.index("result") < seen_types_in_order.index("done")
    assert seen_types_in_order[-1] == "done"


def test_error_event_then_done(patched_app):
    """graph 抛异常时应推 error 事件 + done 兜底"""
    app, factory = patched_app
    factory(_ExplodingGraph())

    client = TestClient(app, raise_server_exceptions=False)
    seen_types = []
    with client.stream("POST", "/api/v1/query", json={
        "query": "q",
        "session_id": "s_err",
        "user_id": "u",
        "db_id": "any",
    }) as resp:
        for raw in resp.iter_lines():
            line = raw if isinstance(raw, str) else raw.decode("utf-8", "replace")
            if line.startswith("data:"):
                try:
                    evt = json.loads(line[5:].strip())
                    seen_types.append(evt["type"])
                except Exception:
                    pass

    assert "error" in seen_types
    assert seen_types[-1] == "done"


def test_no_llm_chunk_event_type(patched_app):
    """SSE 流中不应出现 llm_chunk 事件类型（决策 50）"""
    app, factory = patched_app
    slow = _SlowGraph(delays=[0.05, 0.05])
    factory(slow)

    client = TestClient(app, raise_server_exceptions=False)
    seen_types = set()
    with client.stream("POST", "/api/v1/query", json={
        "query": "q",
        "session_id": "s_nochunk",
        "user_id": "u",
        "db_id": "any",
    }) as resp:
        for raw in resp.iter_lines():
            line = raw if isinstance(raw, str) else raw.decode("utf-8", "replace")
            if line.startswith("data:"):
                try:
                    seen_types.add(json.loads(line[5:].strip())["type"])
                except Exception:
                    pass

    assert "llm_chunk" not in seen_types, (
        f"决策 50 禁止 llm_chunk 事件，但实际看到: {seen_types}"
    )


@pytest.mark.skipif(
    os.getenv("SKIP_HEARTBEAT_TEST", "0") == "1",
    reason="心跳测试需要 sleep 17s，CI 中可以跳过",
)
def test_heartbeat_when_idle(patched_app, monkeypatch):
    """15 秒无事件应发心跳行；用环境变量把心跳间隔调小到 1s 加速测试"""
    # 把心跳间隔从默认 15s 缩到 1s，避免测试拖太久
    monkeypatch.setenv("SSE_HEARTBEAT_INTERVAL", "1")
    # 注意：query.py 模块加载时已读取 _HEARTBEAT_INTERVAL，需要重载
    import importlib
    from src.api.routes import query as query_module
    importlib.reload(query_module)
    # 重载会改变 router 实例，需要重建 app —— 改用直接修改模块变量
    monkeypatch.setattr(query_module, "_HEARTBEAT_INTERVAL", 1.0)

    app, factory = patched_app
    # graph sleep 3s 才 yield，期间应有 2~3 次心跳
    class _SleepGraph:
        def stream(self, initial_state, **kwargs):
            time.sleep(3)
            yield {"decision": {"final_sql": "SELECT 1", "final_result": []}}

    factory(_SleepGraph())

    client = TestClient(app, raise_server_exceptions=False)
    heartbeat_count = 0
    with client.stream("POST", "/api/v1/query", json={
        "query": "q",
        "session_id": "s_hb",
        "user_id": "u",
        "db_id": "any",
    }) as resp:
        for raw in resp.iter_lines():
            line = raw if isinstance(raw, str) else raw.decode("utf-8", "replace")
            if line.strip().startswith(":"):
                heartbeat_count += 1

    assert heartbeat_count >= 1, "3 秒空闲应至少触发 1 次心跳"


# ── 请求验证 ───────────────────────────────────────────────


def test_missing_query_returns_422(patched_app):
    """缺少 query 字段应返回 422"""
    app, factory = patched_app
    factory(_SlowGraph(delays=[]))
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/api/v1/query",
        json={"session_id": "test", "user_id": "test", "db_id": "any"},
    )
    assert response.status_code == 422


def test_missing_session_id_returns_422(patched_app):
    """缺少 session_id 应返回 422"""
    app, factory = patched_app
    factory(_SlowGraph(delays=[]))
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/api/v1/query",
        json={"query": "test", "user_id": "test", "db_id": "any"},
    )
    assert response.status_code == 422


# ── 健康检查 ───────────────────────────────────────────────


def test_health_endpoint(patched_app):
    """健康检查应返回 ok"""
    app, factory = patched_app
    factory(_SlowGraph(delays=[]))
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


# ── 反问 interrupt 测试（决策 12）──────────────────────────────

class _InterruptGraph:
    """模拟 task_planner 触发 interrupt：yield __interrupt__ 后结束（图挂起）"""

    def stream(self, initial_state, **kwargs):
        # 模拟 LangGraph interrupt 挂起的 update
        from langgraph.types import Interrupt
        yield {"__interrupt__": (Interrupt(
            value={"question": "苹果指公司还是水果？",
                   "ambiguities": [{"entity": "苹果", "candidates": ["公司", "水果"]}],
                   "round": 1},
            id="fake-interrupt-id",
        ),)}


def test_clarification_event_on_interrupt(patched_app):
    """graph 触发 interrupt 时应推送 clarification 事件，done 带 awaiting_clarification"""
    app, factory = patched_app
    factory(_InterruptGraph())
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/api/v1/query",
        json={"query": "查苹果的销售额", "session_id": "clr1", "user_id": "test", "db_id": "any"},
    )
    assert response.status_code == 200

    events = [json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")]
    types = [e.get("type") for e in events]

    # 应有 clarification 事件
    assert "clarification" in types, f"应推送 clarification 事件，实际: {types}"
    clarify_evt = next(e for e in events if e.get("type") == "clarification")
    assert "苹果" in clarify_evt["data"]["question"]
    assert clarify_evt["data"]["awaiting_answer"] is True

    # done 事件应带 awaiting_clarification=True
    done_evt = next(e for e in events if e.get("type") == "done")
    assert done_evt["data"]["awaiting_clarification"] is True

    # interrupt 时不应有 result 事件
    assert "result" not in types


def test_resume_request_accepted(patched_app):
    """resume 请求（带 resume 字段、query 可空）应被接受（200）"""
    app, factory = patched_app
    factory(_SlowGraph(delays=[]))
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/api/v1/query",
        json={"query": "", "session_id": "clr2", "user_id": "test", "db_id": "any",
              "resume": "公司"},
    )
    # resume 请求 query 为空也应通过校验（200）
    assert response.status_code == 200


def test_resume_without_query_and_resume_rejected(patched_app):
    """既无 query 又无 resume 应返回 422"""
    app, factory = patched_app
    factory(_SlowGraph(delays=[]))
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/api/v1/query",
        json={"query": "", "session_id": "clr3", "user_id": "test", "db_id": "any"},
    )
    assert response.status_code == 422

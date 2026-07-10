"""多数据库查询接口测试（决策 49）

验证：
1. POST /query body 必须包含 db_id（缺失返回 422）
2. 不同 db_id 走不同的 DbContext（通过 pool.acquire 路由）
3. SSE 流式响应能正确产出 stage / cache_check / result / done 事件
4. 查询完成后 pool.release 被调用（refcount 回到 0）

不依赖真实 BGE / 真实数据库 / 真实 LLM —— 用 MagicMock 替换全局组件。
"""

import json
import threading
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ── Mock 一个最小的 Graph ──────────────────────────────────────

class _MockGraph:
    def __init__(self, db_id: str):
        self._db_id = db_id

    def stream(self, initial_state, **kwargs):
        yield {"history_cache": {"cache_hit": False, "cache_confidence": 0.0}}
        yield {"ir": {"keywords": ["mock"]}}
        yield {"ss": {"selected_schema": ["t"]}}
        yield {"cg": {"sql_candidates": ["SELECT 1"]}}
        yield {"execution": {"sql_candidates": ["SELECT 1"]}}
        yield {
            "decision": {
                "final_sql": f"SELECT * FROM {self._db_id}_table",
                "final_result": [{"col": 1}],
            }
        }
        yield {"memory_update": {}}


# ── Fixtures ──────────────────────────────────────────────────

@pytest.fixture
def patched_app(tmp_path, monkeypatch):
    """
    用 mock 全局组件 + mock DbContextPool 替换 deps，绕过 init_globals 真实加载。
    """
    from src.api import deps

    # 准备 SessionManager（用临时目录避免污染）
    from src.memory.session_manager import SessionManager
    sm = SessionManager(base_dir=str(tmp_path / "sessions"), max_cache_size=20)

    # mock UserMemory
    class _UM:
        def get_metric_definitions(self, min_confidence=0.7):
            return []
        def get_query_preferences(self):
            return {}

    # mock pool
    class _MockPool:
        def __init__(self):
            self._acquired = {}
            self._released = {}
            self._lock = threading.Lock()

        def acquire(self, db_id):
            with self._lock:
                self._acquired[db_id] = self._acquired.get(db_id, 0) + 1
            ctx = MagicMock()
            ctx.graph = _MockGraph(db_id)
            return ctx

        def release(self, db_id):
            with self._lock:
                self._released[db_id] = self._released.get(db_id, 0) + 1

        def stats(self):
            return {"max": 2, "size": 0, "cached": []}

    mock_pool = _MockPool()

    # 注入到 deps
    monkeypatch.setattr(deps, "_session_manager", sm)
    monkeypatch.setattr(deps, "_db_pool", mock_pool)
    monkeypatch.setattr(deps, "_globals", MagicMock(data_dir=str(tmp_path), memory_dir=str(tmp_path / "memory")))
    monkeypatch.setattr(deps, "_user_memory_cache", {})
    # patch get_user_memory 避免触碰文件系统
    monkeypatch.setattr(deps, "get_user_memory", lambda user_id: _UM())

    # 跳过 lifespan 真实加载
    from src.api.app import app

    return app, mock_pool, sm


# ── Tests ─────────────────────────────────────────────────────

def test_query_requires_db_id(patched_app):
    """缺少 db_id 时返回 422"""
    app, _, _ = patched_app
    # 用 lifespan_context=False 等价于跳过 lifespan
    client = TestClient(app, raise_server_exceptions=False)
    # FastAPI TestClient 默认会触发 lifespan；用 client 之前覆盖 deps 即可
    resp = client.post("/api/v1/query", json={
        "query": "hello",
        "session_id": "s1",
        "user_id": "alice",
        # 缺 db_id
    })
    assert resp.status_code == 422
    body = resp.json()
    assert "db_id" in json.dumps(body), "错误应明确指出 db_id 缺失"


def test_query_releases_after_done(patched_app):
    """SSE 流结束后 pool.release 被调用"""
    app, mock_pool, _ = patched_app
    client = TestClient(app, raise_server_exceptions=False)
    with client.stream("POST", "/api/v1/query", json={
        "query": "查一下学生数",
        "session_id": "s2",
        "user_id": "alice",
        "db_id": "california_schools",
    }) as resp:
        # 消费流
        body_text = "".join(resp.iter_text())

    assert mock_pool._acquired.get("california_schools", 0) == 1
    assert mock_pool._released.get("california_schools", 0) == 1, \
        "查询结束后必须 release 一次"
    # SSE 事件序列存在
    assert "data:" in body_text
    assert "\"type\": \"done\"" in body_text


def test_query_switch_db_id_routes_to_different_ctx(patched_app):
    """同一 session 切 db_id 不会污染"""
    app, mock_pool, sm = patched_app
    client = TestClient(app, raise_server_exceptions=False)

    for db_id in ("california_schools", "financial"):
        with client.stream("POST", "/api/v1/query", json={
            "query": "test",
            "session_id": "shared_session",
            "user_id": "alice",
            "db_id": db_id,
        }) as resp:
            "".join(resp.iter_text())

    assert mock_pool._acquired.get("california_schools") == 1
    assert mock_pool._acquired.get("financial") == 1
    assert mock_pool._released.get("california_schools") == 1
    assert mock_pool._released.get("financial") == 1


def test_query_sse_contains_result_event(patched_app):
    """SSE 输出中包含 result 事件，且 sql 与 db_id 关联"""
    app, _, _ = patched_app
    client = TestClient(app, raise_server_exceptions=False)
    with client.stream("POST", "/api/v1/query", json={
        "query": "x",
        "session_id": "s3",
        "user_id": "u1",
        "db_id": "card_games",
    }) as resp:
        body = "".join(resp.iter_text())
    assert "\"type\": \"result\"" in body
    assert "card_games_table" in body, "result.sql 应来自 mock graph"

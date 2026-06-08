"""查询接口测试

使用 Mock 组件初始化主图，测试 API 路由和 SSE 流式输出。
"""

import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.app import app
from src.api.deps import init_components


# ── Mock 组件 ─────────────────────────────────────────────────

class MockGraph:
    """模拟 LangGraph CompiledGraph"""

    def stream(self, initial_state, **kwargs):
        """模拟 stream() 输出"""
        yield {"history_cache": {"cache_hit": False, "trace_log": ["[HistoryCache] disabled"]}}
        yield {"ir": {"keywords": [], "retrieved_context": None, "trace_log": ["[IR] done"]}}
        yield {"clarification": {"clarification_done": True, "trace_log": ["[Clarification] skipped"]}}
        yield {"ss": {"selected_schema": [], "trace_log": ["[SS] done"]}}
        yield {"cg": {"sql_candidates": [], "trace_log": ["[CG] done"]}}
        yield {"execution": {"sql_candidates": [], "trace_log": ["[Execution] done"]}}
        yield {"decision": {"final_sql": "", "final_result": None, "trace_log": ["[Decision] done"]}}
        yield {"memory_update": {"trace_log": ["[MemoryUpdate] done"]}}


# ── Fixtures ──────────────────────────────────────────────────

@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def setup_mocks():
    """每个测试前用 Mock 组件初始化"""
    init_components(
        retriever=MagicMock(),
        selector=MagicMock(),
        generator=MagicMock(),
        fix_loop=MagicMock(),
        decider=MagicMock(),
    )
    # 替换 _graph 为 MockGraph（stream 需要特定输出）
    from src.api import deps
    deps._graph = MockGraph()
    yield


# ── Test: SSE 流式响应 ───────────────────────────────────────

class TestQuerySSE:
    """SSE 流式响应测试"""

    def test_sse_response_content_type(self, client):
        """响应应为 text/event-stream"""
        response = client.post(
            "/api/v1/query",
            json={"query": "测试查询", "session_id": "test-session", "user_id": "test"},
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")

    def test_sse_contains_done_event(self, client):
        """SSE 响应应包含 done 事件"""
        response = client.post(
            "/api/v1/query",
            json={"query": "测试查询", "session_id": "test-session", "user_id": "test"},
        )
        text = response.text
        assert "done" in text

    def test_sse_contains_stage_events(self, client):
        """SSE 响应应包含 stage 事件"""
        response = client.post(
            "/api/v1/query",
            json={"query": "测试查询", "session_id": "test-session", "user_id": "test"},
        )
        text = response.text
        assert "stage" in text
        assert '"node":' in text

    def test_sse_events_are_valid_json(self, client):
        """每个 SSE 事件应是有效 JSON"""
        response = client.post(
            "/api/v1/query",
            json={"query": "测试查询", "session_id": "test-session", "user_id": "test"},
        )
        for line in response.text.strip().split("\n"):
            if line.startswith("data: "):
                data = line[6:]
                event = json.loads(data)
                assert "type" in event
                assert "data" in event

    def test_cache_check_event_included(self, client):
        """SSE 响应应包含 cache_check 事件"""
        response = client.post(
            "/api/v1/query",
            json={"query": "测试查询", "session_id": "test-session", "user_id": "test"},
        )
        assert "cache_check" in response.text


# ── Test: 请求验证 ────────────────────────────────────────────

class TestRequestValidation:
    """请求验证测试"""

    def test_missing_query(self, client):
        """缺少 query 字段应返回 422"""
        response = client.post(
            "/api/v1/query",
            json={"session_id": "test", "user_id": "test"},
        )
        assert response.status_code == 422

    def test_missing_session_id(self, client):
        """缺少 session_id 应返回 422"""
        response = client.post(
            "/api/v1/query",
            json={"query": "test", "user_id": "test"},
        )
        assert response.status_code == 422


# ── Test: 健康检查 ────────────────────────────────────────────

class TestHealth:
    """健康检查测试"""

    def test_health_endpoint(self, client):
        """健康检查应返回 ok"""
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

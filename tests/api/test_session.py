"""会话管理接口测试

直接 mock src.api.deps 模块级的全局变量，测试会话 CRUD 接口。
"""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api import deps
from src.api.app import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def mock_deps():
    """Mock SessionManager 全局实例"""
    sm = MagicMock()

    # Mock list_sessions
    sm.list_sessions.return_value = [
        {
            "session_id": "sess-001",
            "user_id": "alice",
            "created_at": "2026-06-05T10:00:00",
            "updated_at": "2026-06-05T10:05:00",
            "status": "active",
            "turn_count": 3,
        },
        {
            "session_id": "sess-002",
            "user_id": "alice",
            "created_at": "2026-06-04T10:00:00",
            "updated_at": "2026-06-04T11:00:00",
            "status": "active",
            "turn_count": 1,
        },
    ]

    # Mock get_session
    mock_session = MagicMock()
    mock_session.get_turn_count.return_value = 2
    mock_session.get_recent_turns.return_value = [
        {"user_query": "查询A", "final_sql": "SELECT * FROM A"},
        {"user_query": "查询B", "final_sql": "SELECT * FROM B"},
    ]
    sm.get_session.return_value = mock_session

    # Mock delete_session
    sm.delete_session.return_value = True

    # Mock EventCacheStore（change session-restore-event-cache）
    ec = MagicMock()
    ec.list_sessions_paged.return_value = {
        "page": 0,
        "size": 20,
        "has_more": False,
        "sessions": [
            {
                "session_id": "sess-001",
                "user_id": "alice",
                "created_at": "2026-06-05T10:00:00",
                "updated_at": "2026-06-05T10:05:00",
                "status": "active",
                "turn_count": 3,
            },
            {
                "session_id": "sess-002",
                "user_id": "alice",
                "created_at": "2026-06-04T10:00:00",
                "updated_at": "2026-06-04T11:00:00",
                "status": "active",
                "turn_count": 1,
            },
        ],
    }
    ec.get_session_events.return_value = None  # 默认无事件流，回落 session_memory 摘要
    ec.register_session.return_value = "shard_0001"

    deps._session_manager = sm
    deps._event_cache = ec
    yield
    deps._session_manager = None
    deps._event_cache = None


class TestSessionList:
    """会话列表测试"""

    def test_list_sessions(self, client):
        response = client.get("/api/v1/sessions", params={"user_id": "alice"})
        assert response.status_code == 200
        data = response.json()
        assert "sessions" in data
        assert len(data["sessions"]) == 2

    def test_list_sessions_missing_user(self, client):
        response = client.get("/api/v1/sessions")
        assert response.status_code == 422


class TestSessionHistory:
    """会话历史测试"""

    def test_get_history(self, client):
        response = client.get(
            "/api/v1/sessions/sess-001/history",
            params={"user_id": "alice"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == "sess-001"
        assert len(data["turns"]) == 2

    def test_get_history_not_found(self, client):
        deps._session_manager.get_session.return_value = None
        response = client.get(
            "/api/v1/sessions/nonexistent/history",
            params={"user_id": "alice"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "error" in data


class TestSessionDelete:
    """会话删除测试"""

    def test_delete_session(self, client):
        response = client.delete(
            "/api/v1/sessions/sess-001",
            params={"user_id": "alice"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deleted"

    def test_delete_not_found(self, client):
        deps._session_manager.delete_session.return_value = False
        response = client.delete(
            "/api/v1/sessions/nonexistent",
            params={"user_id": "alice"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "error" in data


class TestEventCacheIntegration:
    """change session-restore-event-cache：event_cache 集成测试"""

    def test_create_session_double_write(self, client):
        """create_session 双写 session_memory（复用层）+ event_cache（展示层索引）"""
        new_session = MagicMock()
        new_session.session_id = "new-sess"
        deps._session_manager.create_session.return_value = new_session
        response = client.post("/api/v1/sessions", json={"user_id": "alice"})
        assert response.status_code == 200
        assert response.json()["session_id"] == "new-sess"
        deps._session_manager.create_session.assert_called_once_with(user_id="alice")
        deps._event_cache.register_session.assert_called_once_with("alice", "new-sess")

    def test_list_sessions_paged_fields(self, client):
        """list_sessions 返回分页字段 page/size/has_more（D6）"""
        response = client.get("/api/v1/sessions", params={"user_id": "alice"})
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 0
        assert data["has_more"] is False
        assert len(data["sessions"]) == 2

    def test_get_history_events_source(self, client):
        """有事件流时 source=events（D2，前端重放）"""
        deps._event_cache.get_session_events.return_value = {
            "session_id": "sess-001",
            "turns": [{"turn_index": 1, "events": [{"type": "result", "data": {"sql": "SELECT 1", "result": []}}]}],
            "has_events": True,
        }
        response = client.get("/api/v1/sessions/sess-001/history", params={"user_id": "alice"})
        assert response.status_code == 200
        data = response.json()
        assert data["source"] == "events"
        assert data["has_events"] is True

    def test_get_history_summary_fallback(self, client):
        """无事件流回落 session_memory 摘要 source=summary（老会话兼容，D8）"""
        deps._event_cache.get_session_events.return_value = None
        response = client.get("/api/v1/sessions/sess-001/history", params={"user_id": "alice"})
        assert response.status_code == 200
        data = response.json()
        assert data["source"] == "summary"
        assert data["has_events"] is False
        assert len(data["turns"]) == 2  # session_memory 的 2 轮

    def test_delete_session_cleans_event_cache(self, client):
        """delete 同步清理 event_cache（5.4）"""
        response = client.delete("/api/v1/sessions/sess-001", params={"user_id": "alice"})
        assert response.status_code == 200
        deps._event_cache.delete_session.assert_called_once_with("alice", "sess-001")

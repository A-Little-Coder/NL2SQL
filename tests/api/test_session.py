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

    deps._session_manager = sm
    yield
    deps._session_manager = None


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

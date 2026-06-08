"""用户记忆接口测试

Mock UserMemory，测试用户记忆查询接口。
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
def mock_user_memory():
    """Mock UserMemory 全局缓存"""
    mock_um = MagicMock()
    mock_um._data = {
        "term_preferences": {},
        "frequently_used_tables": {"users": {"query_count": 5, "last_used": "2026-06-05"}},
        "metric_definitions": {
            "销售额": {"description": "SUM of amount", "sql_pattern": "SELECT SUM(amount) FROM sales", "source": "auto_learned", "confidence": 0.7},
        },
        "query_preferences": {"default_sort": "DESC"},
        "domain_context": {"industry": "retail"},
        "clarification_history": [],
    }
    mock_um.get_metric_definitions.return_value = [
        {"name": "销售额", "description": "SUM of amount", "sql_pattern": "SELECT SUM(amount) FROM sales", "confidence": 0.7},
    ]
    mock_um.get_query_preferences.return_value = {"default_sort": "DESC"}
    mock_um.get_domain_context.return_value = {"industry": "retail"}

    deps._user_memory_cache["test_user"] = mock_um
    yield
    deps._user_memory_cache.clear()


class TestUserMemory:
    """用户记忆测试"""

    def test_get_memory(self, client):
        response = client.get("/api/v1/users/test_user/memory")
        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == "test_user"
        assert "frequently_used_tables" in data
        assert "metric_definitions" in data
        assert "query_preferences" in data

    def test_get_metrics(self, client):
        response = client.get("/api/v1/users/test_user/metrics")
        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == "test_user"
        assert len(data["metrics"]) >= 1

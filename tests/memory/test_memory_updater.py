"""MemoryUpdater 单元测试

覆盖场景：
- 常用表学习：从 final_sql 提取表名
- 指标定义学习：检测简单聚合 SQL，提取指标定义
- 查询偏好学习：检测排序/limit/分组偏好
- 会话上下文更新
- 无 SQL 时跳过
"""

from unittest.mock import MagicMock

import pytest

from src.memory.memory_updater import MemoryUpdater
from src.memory.user_memory import UserMemory


@pytest.fixture
def user_memory(tmp_path):
    um = UserMemory(user_id="test_user", base_dir=str(tmp_path / "user_memory"))
    um.load()
    return um


@pytest.fixture
def session_memory(tmp_path):
    """创建一个简单的 SessionMemory mock"""
    class FakeSessionMemory:
        def __init__(self):
            self.summary = {}

        def update_context_summary(self, summary):
            self.summary = summary

        def get_context_summary(self):
            return self.summary

    return FakeSessionMemory()


@pytest.fixture
def updater():
    return MemoryUpdater(llm_client=None)


@pytest.fixture
def state_with_sql():
    return {
        "user_query": "查询苹果的销售额",
        "final_sql": "SELECT SUM(amount) FROM sales WHERE product='Apple'",
        "clarification_history": [],
    }


# ── Test: 常用表学习 ──────────────────────────────────────────

class TestTableUsage:
    """从 SQL 中学习常用表"""

    def test_extract_tables(self, user_memory, session_memory, updater):
        """单表 SQL 应正确提取"""
        state = {
            "user_query": "test",
            "final_sql": "SELECT * FROM users",
            "clarification_history": [],
        }
        updater.update(user_memory, session_memory, state)
        tables = user_memory.get_frequently_used_tables()
        assert "users" in tables

    def test_multi_table(self, user_memory, session_memory, updater):
        """多表 SQL 应提取所有表名"""
        state = {
            "user_query": "test",
            "final_sql": "SELECT u.name, o.total FROM users u JOIN orders o ON u.id=o.user_id",
            "clarification_history": [],
        }
        updater.update(user_memory, session_memory, state)
        tables = user_memory.get_frequently_used_tables()
        assert "users" in tables
        assert "orders" in tables

    def test_no_sql_skip(self, user_memory, session_memory, updater):
        """无 final_sql 时应跳过"""
        state = {
            "user_query": "test",
            "final_sql": "",
            "clarification_history": [],
        }
        updater.update(user_memory, session_memory, state)
        tables = user_memory.get_frequently_used_tables()
        assert tables == []

    def test_increment_count(self, user_memory, session_memory, updater):
        """同一表多次出现应累加计数"""
        state = {
            "user_query": "test",
            "final_sql": "SELECT * FROM users",
            "clarification_history": [],
        }
        updater.update(user_memory, session_memory, state)
        updater.update(user_memory, session_memory, state)
        updater.update(user_memory, session_memory, state)

        tables = user_memory.get_frequently_used_tables(top_k=10)
        assert tables.count("users") == 1  # 去重返回
        # 验证计数通过直接读取
        memory_data = user_memory._data
        assert memory_data["frequently_used_tables"]["users"]["query_count"] >= 3


# ── Test: 指标定义学习 ────────────────────────────────────────

class TestMetricLearning:
    """从聚合 SQL 学习指标定义"""

    def test_sum_metric(self, user_memory, session_memory, updater):
        """SUM 聚合应提取为指标"""
        state = {
            "user_query": "查询总销售额",
            "final_sql": "SELECT SUM(amount) FROM sales",
            "clarification_history": [],
        }
        updater.update(user_memory, session_memory, state)
        metrics = user_memory.get_metric_definitions(min_confidence=0.0)
        assert len(metrics) >= 1
        assert any("SUM" in m.get("sql_pattern", "").upper() for m in metrics)

    def test_count_metric(self, user_memory, session_memory, updater):
        """COUNT 聚合应提取为指标"""
        state = {
            "user_query": "统计客户数",
            "final_sql": "SELECT COUNT(customer_id) FROM customers",
            "clarification_history": [],
        }
        updater.update(user_memory, session_memory, state)
        metrics = user_memory.get_metric_definitions(min_confidence=0.0)
        assert len(metrics) >= 1
        assert any("COUNT" in m.get("sql_pattern", "").upper() for m in metrics)

    def test_avg_metric(self, user_memory, session_memory, updater):
        """AVG 聚合应提取为指标"""
        state = {
            "user_query": "计算平均分",
            "final_sql": "SELECT AVG(score) FROM grades",
            "clarification_history": [],
        }
        updater.update(user_memory, session_memory, state)
        metrics = user_memory.get_metric_definitions(min_confidence=0.0)
        assert len(metrics) >= 1

    def test_non_aggregate_skip(self, user_memory, session_memory, updater):
        """非聚合 SQL 应跳过指标学习"""
        state = {
            "user_query": "查询用户列表",
            "final_sql": "SELECT name, email FROM users",
            "clarification_history": [],
        }
        updater.update(user_memory, session_memory, state)
        metrics = user_memory.get_metric_definitions(min_confidence=0.0)
        # 非聚合 SQL 可能不会创建指标
        assert len(metrics) == 0


# ── Test: 查询偏好学习 ────────────────────────────────────────

class TestQueryPreferences:
    """从 SQL 中学习查询偏好"""

    def test_desc_sort(self, user_memory, session_memory, updater):
        """ORDER BY DESC 应记录排序偏好"""
        state = {
            "user_query": "test",
            "final_sql": "SELECT name FROM users ORDER BY created_at DESC",
            "clarification_history": [],
        }
        updater.update(user_memory, session_memory, state)
        prefs = user_memory.get_query_preferences()
        assert prefs.get("default_sort") == "DESC"

    def test_asc_sort(self, user_memory, session_memory, updater):
        """ORDER BY ASC 应记录排序偏好"""
        state = {
            "user_query": "test",
            "final_sql": "SELECT name FROM users ORDER BY name ASC",
            "clarification_history": [],
        }
        updater.update(user_memory, session_memory, state)
        prefs = user_memory.get_query_preferences()
        assert prefs.get("default_sort") == "ASC"

    def test_limit_detection(self, user_memory, session_memory, updater):
        """LIMIT 应记录行数偏好"""
        state = {
            "user_query": "test",
            "final_sql": "SELECT name FROM users LIMIT 10",
            "clarification_history": [],
        }
        updater.update(user_memory, session_memory, state)
        prefs = user_memory.get_query_preferences()
        assert prefs.get("default_limit") == "10"


# ── Test: 会话上下文更新 ──────────────────────────────────────

class TestSessionContext:
    """会话上下文更新"""

    def test_context_summary_updated(self, user_memory, session_memory, updater):
        """会话摘要应包含查询主题和表名"""
        state = {
            "user_query": "查询苹果的销售额",
            "final_sql": "SELECT SUM(amount) FROM sales WHERE product='Apple'",
            "clarification_history": [],
        }
        updater.update(user_memory, session_memory, state)
        summary = session_memory.get_context_summary()
        assert "last_topic" in summary
        assert "last_tables" in summary
        assert "sales" in summary.get("last_tables", [])

    def test_no_query_skip(self, user_memory, session_memory, updater):
        """无查询时跳过上下文更新"""
        state = {
            "user_query": "",
            "final_sql": "",
            "clarification_history": [],
        }
        updater.update(user_memory, session_memory, state)
        summary = session_memory.get_context_summary()
        assert summary == {}


# ── Test: Clarification 历史写入 ─────────────────────────────

class TestClarificationWrite:
    """Clarification 历史写入"""

    def test_write_clarification_history(self, user_memory, session_memory, updater):
        """clarification_history 应写入 UserMemory"""
        state = {
            "user_query": "test",
            "final_sql": "SELECT 1",
            "clarification_history": [
                {"question": "您指的是哪个产品？", "answer": "苹果"},
            ],
        }
        updater.update(user_memory, session_memory, state)
        # 验证写入（通过直接读取内部数据）
        assert len(user_memory._data.get("clarification_history", [])) == 1
        entry = user_memory._data["clarification_history"][0]
        assert entry["question"] == "您指的是哪个产品？"


# ── Test: Updater 无 LLM 降级 ────────────────────────────────

class TestUpdaterNoLLM:
    """无 LLM 时的降级行为"""

    def test_no_llm_uses_simple_extraction(self, user_memory, session_memory):
        """llm_client=None 时使用简单规则提取指标"""
        updater = MemoryUpdater(llm_client=None)
        state = {
            "user_query": "查询总销售额",
            "final_sql": "SELECT SUM(amount) FROM sales",
            "clarification_history": [],
        }
        updater.update(user_memory, session_memory, state)
        metrics = user_memory.get_metric_definitions(min_confidence=0.0)
        assert len(metrics) >= 1
        # 简单提取使用 SUM_amount 命名
        assert any("SUM" in m.get("name", "") for m in metrics)


class TestSessionRecallWrite:
    """SessionMemory v2 写入策略"""

    def test_success_query_writes_session_recall_memory(self, user_memory, session_memory):
        from src.memory.session_recall import HybridSessionRetriever, JsonConversationStore, SessionRecallConfig

        class FakeIndex:
            def __init__(self):
                self.items = []

            def upsert(self, memory):
                self.items.append(memory)
                return True

            def query_dense(self, *args, **kwargs):
                return []

        index = FakeIndex()
        store = JsonConversationStore("data/test_session_recall_tmp")
        retriever = HybridSessionRetriever(index, store, SessionRecallConfig())
        updater = MemoryUpdater(llm_client=None, session_retriever=retriever)
        session_memory.session_id = "s1"
        session_memory.get_turn_count = lambda: 0
        state = {
            "user_query": "查询苹果销售额",
            "user_id": "u1",
            "database_filter": "db1",
            "final_sql": "SELECT SUM(amount) FROM sales",
            "final_result": [{"x": 1}],
            "clarification_history": [],
        }

        updater.update(user_memory, session_memory, state)

        assert len(index.items) == 1
        assert index.items[0].session_id == "s1"
        assert index.items[0].db_id == "db1"

    def test_failed_query_does_not_write_session_recall_memory(self, user_memory, session_memory):
        class FakeRetriever:
            def __init__(self):
                self.query_index = MagicMock()
                self.conversation_store = MagicMock()

        retriever = FakeRetriever()
        updater = MemoryUpdater(llm_client=None, session_retriever=retriever)
        session_memory.session_id = "s1"
        state = {
            "user_query": "查询苹果销售额",
            "user_id": "u1",
            "database_filter": "db1",
            "final_sql": "",
            "error": "failed",
            "clarification_history": [],
        }

        updater.update(user_memory, session_memory, state)

        retriever.query_index.upsert.assert_not_called()
        retriever.conversation_store.upsert_turn.assert_not_called()

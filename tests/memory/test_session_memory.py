"""SessionMemory 单元测试"""

import shutil
import tempfile
from pathlib import Path

import pytest

from src.memory.session_memory import SessionMemory
from src.memory.storage import Storage


@pytest.fixture
def temp_dir():
    path = tempfile.mkdtemp()
    yield path
    shutil.rmtree(path)


@pytest.fixture
def storage(temp_dir):
    return Storage(temp_dir)


@pytest.fixture
def session(storage):
    mem = SessionMemory("test-session", "test_user", storage)
    mem.load()
    return mem


class TestSessionMemoryBasic:
    """基础操作测试"""

    def test_create_and_load(self, storage):
        """新会话首次加载应创建初始文件"""
        mem = SessionMemory("session-001", "alice", storage)
        data = mem.load()

        assert data["session_id"] == "session-001"
        assert data["user_id"] == "alice"
        assert data["status"] == "active"
        assert data["conversation_history"] == []
        assert data["context_summary"] == {}

        # 验证文件已创建
        file_path = storage.session_path("alice", "session-001")
        assert file_path.exists()

    def test_load_existing(self, session):
        """加载已有会话不应丢失数据"""
        session.add_turn({"user_query": "测试查询"})

        # 重新加载
        mem2 = SessionMemory("test-session", "test_user", session._storage)
        data2 = mem2.load()
        assert len(data2["conversation_history"]) == 1

    def test_empty_turn_count(self, session):
        assert session.get_turn_count() == 0


class TestAddTurn:
    """追加对话轮次测试"""

    def test_add_first_turn(self, session):
        session.add_turn({
            "user_query": "查一下苹果的销售额",
            "resolved_keywords": ["苹果", "销售额"],
            "final_sql": "SELECT gmv FROM sales",
            "tables_used": ["sales"],
        })
        assert session.get_turn_count() == 1
        turn = session.get_last_turn()
        assert turn["turn_index"] == 1
        assert turn["user_query"] == "查一下苹果的销售额"

    def test_add_multiple_turns(self, session):
        for i in range(3):
            session.add_turn({"user_query": f"query_{i}"})
        assert session.get_turn_count() == 3
        assert session.get_last_turn()["turn_index"] == 3

    def test_turn_index_auto_increment(self, session):
        session.add_turn({"user_query": "第一轮"})
        session.add_turn({"user_query": "第二轮"})
        assert session.get_last_turn()["turn_index"] == 2

    def test_turn_has_timestamp(self, session):
        session.add_turn({"user_query": "test"})
        turn = session.get_last_turn()
        assert "timestamp" in turn

    def test_persist_after_add(self, session):
        """追加后应自动持久化到磁盘"""
        session.add_turn({"user_query": "test"})

        # 新实例从磁盘读取
        mem2 = SessionMemory("test-session", "test_user", session._storage)
        data2 = mem2.load()
        assert len(data2["conversation_history"]) == 1


class TestGetRecentTurns:
    """获取最近对话测试"""

    def test_get_recent_none(self, session):
        assert session.get_recent_turns(3) == []

    def test_get_recent_less_than_n(self, session):
        session.add_turn({"user_query": "q1"})
        recent = session.get_recent_turns(3)
        assert len(recent) == 1

    def test_get_recent_exact_n(self, session):
        for i in range(5):
            session.add_turn({"user_query": f"q{i}"})
        recent = session.get_recent_turns(3)
        assert len(recent) == 3
        assert recent[0]["turn_index"] == 3
        assert recent[1]["turn_index"] == 4
        assert recent[2]["turn_index"] == 5

    def test_get_last_turn_none(self, session):
        assert session.get_last_turn() is None

    def test_get_last_turn(self, session):
        session.add_turn({"user_query": "last"})
        last = session.get_last_turn()
        assert last["user_query"] == "last"


class TestContextSummary:
    """上下文摘要测试"""

    def test_update_context_summary(self, session):
        session.update_context_summary({
            "last_topic": "苹果销售额",
            "last_tables": ["sales"],
        })
        summary = session.get_context_summary()
        assert summary["last_topic"] == "苹果销售额"
        assert summary["last_tables"] == ["sales"]

    def test_update_context_summary_append(self, session):
        session.update_context_summary({"last_topic": "A"})
        session.update_context_summary({"last_time_range": "2025"})
        summary = session.get_context_summary()
        assert summary["last_topic"] == "A"
        assert summary["last_time_range"] == "2025"


class TestFormatForPrompt:
    """Prompt 格式化测试"""

    def test_format_empty(self, session):
        result = session.format_for_prompt()
        assert result == ""

    def test_format_with_history(self, session):
        session.add_turn({
            "user_query": "查苹果销售额",
            "final_sql": "SELECT gmv FROM sales",
            "final_result_sample": [{"gmv": 10000}],
        })
        result = session.format_for_prompt()
        assert "查苹果销售额" in result
        assert "SELECT gmv FROM sales" in result
        assert "10000" in result

    def test_format_with_context_summary(self, session):
        session.add_turn({"user_query": "查苹果"})
        session.update_context_summary({
            "last_topic": "苹果查询",
            "last_tables": ["sales"],
        })
        result = session.format_for_prompt()
        assert "苹果查询" in result
        assert "sales" in result

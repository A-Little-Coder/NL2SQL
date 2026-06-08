"""SessionManager 单元测试"""

import shutil
import tempfile
from pathlib import Path

import pytest

from src.memory.session_manager import SessionManager


@pytest.fixture
def temp_dir():
    path = tempfile.mkdtemp()
    yield path
    shutil.rmtree(path)


@pytest.fixture
def manager(temp_dir):
    return SessionManager(base_dir=temp_dir, max_cache_size=10)


class TestSessionManagerBasic:
    """会话管理器基础测试"""

    def test_create_session(self, manager):
        """创建新会话应生成 UUID 并创建文件"""
        session = manager.create_session(user_id="alice")
        assert session.session_id is not None
        assert session.user_id == "alice"
        assert len(session.session_id) == 36  # UUID 长度

        # 验证文件已创建
        file_path = manager._storage.session_path("alice", session.session_id)
        assert file_path.exists()

    def test_get_session_exists(self, manager):
        """获取已有会话应返回完整数据"""
        created = manager.create_session(user_id="alice")
        created.add_turn({"user_query": "test"})

        loaded = manager.get_session(created.session_id, "alice")
        assert loaded is not None
        assert loaded.get_turn_count() == 1

    def test_get_session_not_exists(self, manager):
        """获取不存在的会话应返回 None"""
        session = manager.get_session("nonexistent-id", "alice")
        assert session is None

    def test_get_session_wrong_user(self, manager):
        """跨用户访问会话应返回 None"""
        created = manager.create_session(user_id="alice")
        session = manager.get_session(created.session_id, "bob")
        assert session is None


class TestMultiSession:
    """多会话测试"""

    def test_list_sessions_empty(self, manager):
        sessions = manager.list_sessions("alice")
        assert sessions == []

    def test_list_sessions(self, manager):
        s1 = manager.create_session("alice")
        s2 = manager.create_session("alice")
        s3 = manager.create_session("alice")
        s1.add_turn({"user_query": "q1"})
        s3.add_turn({"user_query": "q3"})

        sessions = manager.list_sessions("alice")
        assert len(sessions) == 3
        # 按 updated_at 降序
        for i in range(len(sessions) - 1):
            assert sessions[i]["updated_at"] >= sessions[i + 1]["updated_at"]

    def test_user_isolation(self, manager):
        """会话不跨用户"""
        manager.create_session("alice")
        manager.create_session("bob")
        manager.create_session("alice")

        alice_sessions = manager.list_sessions("alice")
        bob_sessions = manager.list_sessions("bob")
        assert len(alice_sessions) == 2
        assert len(bob_sessions) == 1

    def test_list_session_summary(self, manager):
        session = manager.create_session("alice")
        session.add_turn({"user_query": "q1"})

        sessions = manager.list_sessions("alice")
        s = sessions[0]
        assert "session_id" in s
        assert "created_at" in s
        assert "updated_at" in s
        assert "status" in s
        assert "turn_count" in s
        assert s["turn_count"] == 1


class TestDeleteSession:
    """删除会话测试"""

    def test_delete_session(self, manager):
        session = manager.create_session("alice")
        sid = session.session_id

        assert manager.delete_session(sid, "alice") is True
        assert manager.get_session(sid, "alice") is None

    def test_delete_nonexistent(self, manager):
        assert manager.delete_session("nonexistent", "alice") is False

    def test_delete_removes_cache(self, manager):
        session = manager.create_session("alice")
        sid = session.session_id

        # 确保在缓存中
        assert sid in manager._cache

        manager.delete_session(sid, "alice")
        assert sid not in manager._cache


class TestLRUCache:
    """LRU 缓存测试"""

    def test_cache_hit(self, manager):
        session = manager.create_session("alice")
        # 首次从磁盘加载
        s1 = manager.get_session(session.session_id, "alice")
        # 第二次应命中缓存
        s2 = manager.get_session(session.session_id, "alice")
        assert s1 is s2  # 同一个对象

    def test_cache_eviction(self, manager):
        """超过缓存上限应淘汰最久未访问的"""
        # 创建超过上限的会话
        sessions = []
        for i in range(12):  # max_cache_size=10
            s = manager.create_session(f"user_{i}")
            sessions.append(s)

        # 前几个应已被淘汰
        assert sessions[0].session_id not in manager._cache
        # 后几个应仍在缓存中
        assert sessions[11].session_id in manager._cache

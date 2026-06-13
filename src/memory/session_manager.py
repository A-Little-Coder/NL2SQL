"""
会话管理器（SessionManager）

管理用户会话的生命周期：创建、加载、列出、删除。
内部维护 LRU 内存缓存加速热会话访问。
"""

import uuid
from typing import Any, Dict, List, Optional

from .session_memory import SessionMemory
from .storage import Storage


class SessionManager:
    """会话管理器，支持 LRU 缓存和用户隔离"""

    def __init__(self, base_dir: str = "data/sessions", max_cache_size: int = 200):
        self._storage = Storage(base_dir)
        self.max_cache_size = max_cache_size
        # LRU 缓存: {session_id: SessionMemory}
        self._cache: Dict[str, SessionMemory] = {}
        # 访问顺序记录（用于 LRU 淘汰）
        self._access_order: List[str] = []

    # ── LRU 缓存管理 ─────────────────────────────────────────

    def _update_access(self, session_id: str):
        """更新 LRU 访问顺序"""
        if session_id in self._access_order:
            self._access_order.remove(session_id)
        self._access_order.append(session_id)
        # 淘汰最久未访问
        while len(self._access_order) > self.max_cache_size:
            oldest = self._access_order.pop(0)
            self._cache.pop(oldest, None)

    def _cache_get(self, session_id: str) -> Optional[SessionMemory]:
        """从缓存获取，命中则更新访问顺序"""
        mem = self._cache.get(session_id)
        if mem is not None:
            self._update_access(session_id)
        return mem

    def _cache_put(self, session_id: str, mem: SessionMemory):
        """放入缓存"""
        self._cache[session_id] = mem
        self._update_access(session_id)

    def _cache_remove(self, session_id: str):
        """从缓存移除"""
        self._cache.pop(session_id, None)
        if session_id in self._access_order:
            self._access_order.remove(session_id)

    # ── 会话 CRUD ─────────────────────────────────────────────

    def create_session(self, user_id: str) -> SessionMemory:
        """创建新会话，返回 SessionMemory 实例"""
        session_id = str(uuid.uuid4())
        mem = SessionMemory(session_id, user_id, self._storage)
        mem.load()  # 创建初始文件
        self._cache_put(session_id, mem)
        return mem

    def get_session(self, session_id: str, user_id: str) -> Optional[SessionMemory]:
        """
        获取会话

        先从缓存读取，未命中则从磁盘加载。
        校验 user_id 是否匹配（用户隔离）。
        """
        # 先从缓存
        mem = self._cache_get(session_id)
        if mem is not None:
            if mem.user_id != user_id:
                return None
            return mem

        # 从磁盘加载
        file_path = self._storage.session_path(user_id, session_id)
        if not file_path.exists():
            return None

        mem = SessionMemory(session_id, user_id, self._storage)
        data = mem.load()
        # 校验 user_id
        if data.get("user_id") != user_id:
            return None

        self._cache_put(session_id, mem)
        return mem

    def get_or_create_session(self, session_id: str, user_id: str) -> SessionMemory:
        """获取已有会话或创建新会话"""
        mem = self.get_session(session_id, user_id)
        if mem is not None:
            return mem
        # 创建新会话
        mem = SessionMemory(session_id, user_id, self._storage)
        mem.load()
        self._cache_put(session_id, mem)
        return mem

    def list_sessions(self, user_id: str) -> List[Dict[str, Any]]:
        """列出用户的所有会话摘要，按 updated_at 降序"""
        files = self._storage.list_user_session_files(user_id)
        sessions = []
        for f in files:
            data = self._storage.atomic_read(f)
            if data is None:
                continue
            sessions.append({
                "session_id": data.get("session_id", f.stem),
                "user_id": data.get("user_id", user_id),
                "created_at": data.get("created_at", ""),
                "updated_at": data.get("updated_at", ""),
                "status": data.get("status", "unknown"),
                "turn_count": len(data.get("conversation_history", [])),
            })
        # 按 updated_at 降序
        sessions.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
        return sessions

    def delete_session(self, session_id: str, user_id: str) -> bool:
        """删除会话（从磁盘和缓存）"""
        self._cache_remove(session_id)
        file_path = self._storage.session_path(user_id, session_id)
        if file_path.exists():
            file_path.unlink()
            return True
        return False

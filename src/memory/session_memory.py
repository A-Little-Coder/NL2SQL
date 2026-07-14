"""
会话记忆（SessionMemory）

存储一次会话内的多轮对话历史，持久化到 JSON 文件。

文件路径: data/sessions/{user_id}/{session_id}.json

会话记忆不跨用户共享，一个用户可有多个会话。
"""

import copy
from datetime import datetime
from typing import Any, Dict, List, Optional

from .storage import Storage


class SessionMemory:
    """会话记忆，管理单次会话的多轮对话"""

    def __init__(self, session_id: str, user_id: str, storage: Storage):
        self.session_id = session_id
        self.user_id = user_id
        self._storage = storage
        self._file_path = storage.session_path(user_id, session_id)
        self._data: Dict[str, Any] = {}
        self._loaded = False

    # ── 内部结构 ──────────────────────────────────────────────

    @staticmethod
    def _empty_session(session_id: str, user_id: str) -> Dict[str, Any]:
        now = datetime.now().isoformat(timespec="seconds")
        return {
            "session_id": session_id,
            "user_id": user_id,
            "created_at": now,
            "updated_at": now,
            "status": "active",
            "conversation_history": [],
            "context_summary": {},
        }

    def _touch(self):
        self._data["updated_at"] = datetime.now().isoformat(timespec="seconds")

    # ── 加载与保存 ────────────────────────────────────────────

    def load(self) -> Dict[str, Any]:
        """从磁盘加载会话数据"""
        data = self._storage.atomic_read(self._file_path)
        if data is None:
            data = self._empty_session(self.session_id, self.user_id)
            self._storage.atomic_write(self._file_path, data)
        self._data = data
        self._loaded = True
        return self._data

    def save(self):
        """原子写入会话数据到磁盘"""
        self._touch()
        self._storage.atomic_write(self._file_path, self._data)

    # ── 对话轮次管理 ─────────────────────────────────────────

    # 允许写入 turn 的字段白名单（决策 28：会话记忆不存查数结果，避免存储膨胀 + 时效性问题 + 序列化失败）
    _ALLOWED_TURN_FIELDS = {
        "user_query",
        "final_sql",
        "cache_hit",
        "cache_source",
        "rejection_reason",
        "rewrite_rejection_reason",  # Rewrite 改写拒答原因
        "error",
        "result_meta",          # 仅元信息（行数、列名），不含真实数据
        "clarification_round",  # Phase 2 反问轮次
        "final_result_sample",  # Prompt 展示用样例（测试 / Phase 2）
        "reuse_eligible",       # 该轮 SQL 是否可被 history_cache 复用
    }

    def add_turn(self, turn_data: Dict[str, Any]):
        """追加一轮对话，自动持久化

        会按 `_ALLOWED_TURN_FIELDS` 过滤，主动丢弃 final_result 等大字段或非 JSON 可序列化对象，
        防止数据库 Row、numpy 数组等污染会话记忆文件。
        """
        self.load()
        # 字段白名单过滤
        filtered = {k: v for k, v in turn_data.items() if k in self._ALLOWED_TURN_FIELDS}
        dropped = set(turn_data.keys()) - set(filtered.keys())
        if dropped:
            # 不抛错，只警告调用方注意；保持向后兼容
            try:
                from loguru import logger
                logger.warning(
                    f"SessionMemory.add_turn 已过滤未授权字段 {dropped}（会话记忆只保留 SQL 与元信息）"
                )
            except Exception:
                pass

        history = self._data["conversation_history"]
        turn_index = len(history) + 1
        turn = {
            "turn_index": turn_index,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            **filtered,
        }
        history.append(turn)
        self.save()

    def get_recent_turns(self, n: int = 3) -> List[Dict[str, Any]]:
        """获取最近 N 轮对话"""
        self.load()
        history = self._data["conversation_history"]
        return history[-n:] if history else []

    def get_last_turn(self) -> Optional[Dict[str, Any]]:
        """获取上轮对话"""
        self.load()
        history = self._data["conversation_history"]
        return history[-1] if history else None

    def get_turn_count(self) -> int:
        """获取当前对话轮数"""
        self.load()
        return len(self._data["conversation_history"])

    # ── 上下文摘要 ────────────────────────────────────────────

    def update_context_summary(self, summary: Dict[str, Any]):
        """更新上下文摘要"""
        self.load()
        self._data["context_summary"].update(summary)
        self.save()

    def get_context_summary(self) -> Dict[str, Any]:
        """获取上下文摘要"""
        self.load()
        return dict(self._data["context_summary"])

    # ── Prompt 格式化 ─────────────────────────────────────────

    def format_for_prompt(self, max_turns: int = 3) -> str:
        """
        将会话历史格式化为 LLM Prompt 可读文本

        格式:
        ---
        历史对话:
        轮次 1 | 用户: xxx | SQL: xxx | 结果: xxx
        轮次 2 | ...
        ---
        """
        self.load()
        history = self._data["conversation_history"]
        if not history:
            return ""

        recent = history[-max_turns:]
        lines = ["\n---\n历史对话:"]
        for turn in recent:
            idx = turn.get("turn_index", "?")
            query = turn.get("user_query", "")
            sql = turn.get("final_sql", "")
            result = turn.get("final_result_sample", [])
            result_str = str(result[:3]) if result else ""

            line_parts = [f"轮次 {idx}"]
            if query:
                line_parts.append(f"用户: {query}")
            if sql:
                line_parts.append(f"SQL: {sql}")
            if result_str:
                line_parts.append(f"结果: {result_str}")
            lines.append(" | ".join(line_parts))

        summary = self._data.get("context_summary", {})
        if summary:
            topic = summary.get("last_topic", "")
            tables = summary.get("last_tables", [])
            if topic:
                lines.append(f"上下文: {topic}")
            if tables:
                lines.append(f"涉及表: {', '.join(tables)}")

        lines.append("---")
        return "\n".join(lines)

    # ── 序列化 ────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """导出会话数据为字典"""
        self.load()
        return copy.deepcopy(self._data)

    @classmethod
    def from_dict(cls, session_id: str, user_id: str, data: Dict[str, Any],
                  storage: Storage) -> "SessionMemory":
        """从字典恢复会话（用于反序列化）"""
        mem = cls(session_id, user_id, storage)
        mem._data = data
        mem._loaded = True
        return mem

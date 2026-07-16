"""
展示存储层（EventCacheStore）

独立于 session_memory（复用层 data/sessions/），专门服务前端展示恢复。
物理位置：项目根 event_cache/（与 data/ 平级，运行时数据）。

结构：
  event_cache/{user_id}/
    ├── index.json                 会话摘要索引（列表分页用）
    └── shard_xxxx/                按 created_at 分片，每目录 ≤20 会话
        ├── {session_id}.json      单会话事件流
        └── ...

单会话文件结构：
  {
    "session_id": "...",
    "created_at": "...",
    "turns": [ { "turn_index": 1, "timestamp": "...", "events": [...] }, ... ],
    "pending_events": [...]        resume 跨阶段暂存（query 阶段事件）
  }

设计要点（见 change session-restore-event-cache/design.md）：
- D3：按 created_at 分片，会话归属 shard 永久不变；每 shard 目录 ≤20 会话
- D4：result 事件 rows 存储层截断前 20 行（RESULT_ROW_LIMIT），不影响实时推送
- D5：resume 跨阶段--query 阶段（__interrupted__）事件暂存 pending_events，
      resume 完成时合并 pending + resume 事件为一个完整 turn
- D6：index.json 维护摘要，列表分页只读 index（轻），不全量扫会话文件
- 复用 Storage 原子写（tmp->fsync->replace）+ 文件锁

与 session_memory 零耦合：history_cache 复用与混合召回仍走 data/sessions/，
本层只服务前端展示（D1/D9）。
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from .storage import Storage

# 每 shard 目录最多会话数（D3）
SHARD_SIZE = 20
# result 事件行数据存储截断上限（D4）
RESULT_ROW_LIMIT = 20


def _now_iso() -> str:
    """当前 ISO 时间（秒精度）。"""
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


def _truncate_result_rows(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """存储侧截断：result 事件的 data.result 行数据保留前 RESULT_ROW_LIMIT 行。

    返回新列表（不修改入参），非 result 事件原样保留。
    被截断的 result 事件打上 __truncated__=True 标记，供前端显示"历史快照"提示。
    """
    out: List[Dict[str, Any]] = []
    for evt in events:
        if not isinstance(evt, dict) or evt.get("type") != "result":
            out.append(evt)
            continue
        data = dict(evt.get("data") or {})
        rows = data.get("result")
        if isinstance(rows, list):
            truncated = len(rows) > RESULT_ROW_LIMIT
            data["result"] = rows[:RESULT_ROW_LIMIT]
            data["__truncated__"] = truncated
        out.append({"type": "result", "data": data})
    return out


class EventCacheStore:
    """展示存储层：按用户/分片/会话组织的事件流存储。"""

    def __init__(self, base_dir: str = "event_cache"):
        self._storage = Storage(base_dir)

    # ── 路径工具 ─────────────────────────────────────────────

    def _user_dir(self, user_id: str) -> Path:
        return self._storage.base_dir / user_id

    def _index_path(self, user_id: str) -> Path:
        return self._user_dir(user_id) / "index.json"

    def _shard_dir(self, user_id: str, shard_id: str) -> Path:
        return self._user_dir(user_id) / shard_id

    def _session_file(self, user_id: str, shard_id: str, session_id: str) -> Path:
        return self._shard_dir(user_id, shard_id) / f"{session_id}.json"

    # ── index 读写 ───────────────────────────────────────────

    def _read_index(self, user_id: str) -> Dict[str, Any]:
        data = self._storage.atomic_read(self._index_path(user_id))
        if data is None:
            return {"sessions": [], "next_shard_seq": 1}
        data.setdefault("sessions", [])
        data.setdefault("next_shard_seq", 1)
        return data

    def _write_index(self, user_id: str, index: Dict[str, Any]) -> None:
        self._storage.atomic_write(self._index_path(user_id), index)

    @staticmethod
    def _find_session_entry(index: Dict[str, Any], session_id: str) -> Optional[Dict[str, Any]]:
        for s in index["sessions"]:
            if s.get("session_id") == session_id:
                return s
        return None

    def _allocate_shard(self, index: Dict[str, Any]) -> str:
        """分配可写入的 shard_id：最新 shard 未满则复用，否则开新 shard。"""
        shard_counts: Dict[str, int] = {}
        for s in index["sessions"]:
            sid = s.get("shard_id")
            if sid:
                shard_counts[sid] = shard_counts.get(sid, 0) + 1
        if shard_counts:
            latest_shard = sorted(shard_counts.keys(), reverse=True)[0]
            if shard_counts[latest_shard] < SHARD_SIZE:
                return latest_shard
        # 开新 shard
        seq = int(index.get("next_shard_seq", 1))
        index["next_shard_seq"] = seq + 1
        return f"shard_{seq:04d}"

    # ── 会话文件读写 ─────────────────────────────────────────

    def _read_session(self, user_id: str, shard_id: str, session_id: str) -> Dict[str, Any]:
        data = self._storage.atomic_read(self._session_file(user_id, shard_id, session_id))
        if data is None:
            return {"session_id": session_id, "created_at": _now_iso(), "turns": [], "pending_events": []}
        data.setdefault("turns", [])
        data.setdefault("pending_events", [])
        return data

    def _write_session(self, user_id: str, shard_id: str, session_id: str, data: Dict[str, Any]) -> None:
        self._storage.atomic_write(self._session_file(user_id, shard_id, session_id), data)

    # ── 对外 API ─────────────────────────────────────────────

    def register_session(self, user_id: str, session_id: str, created_at: Optional[str] = None) -> str:
        """会话创建时登记：分配 shard、写 index 摘要、建空会话文件。返回 shard_id。

        幂等：若已登记则直接返回现有 shard_id。
        """
        ts = created_at or _now_iso()
        index = self._read_index(user_id)
        existing = self._find_session_entry(index, session_id)
        if existing:
            return existing["shard_id"]
        shard_id = self._allocate_shard(index)
        index["sessions"].append({
            "session_id": session_id,
            "created_at": ts,
            "updated_at": ts,
            "status": "active",
            "turn_count": 0,
            "shard_id": shard_id,
        })
        self._write_index(user_id, index)
        self._write_session(user_id, shard_id, session_id, {
            "session_id": session_id,
            "created_at": ts,
            "turns": [],
            "pending_events": [],
        })
        return shard_id

    def store_turn_events(
        self,
        user_id: str,
        session_id: str,
        events: List[Dict[str, Any]],
        is_pending: bool = False,
        user_query: str = "",
    ) -> None:
        """写入一个 turn 的事件流（D5 resume 跨阶段缓冲）。

        - is_pending=True（query 阶段 __interrupted__）：暂存到 pending_events（已截断），
          等 resume 完成时合并。
        - is_pending=False（turn done）：若有 pending_events，合并 pending + 本次 events
          为一个完整 turn 追加到 turns 并清空 pending；否则直接追加为新 turn。
          result 事件行数据在存储侧截断前 RESULT_ROW_LIMIT 行（D4）。
        """
        index = self._read_index(user_id)
        entry = self._find_session_entry(index, session_id)
        if entry is None:
            # 未登记兜底：先登记
            self.register_session(user_id, session_id)
            index = self._read_index(user_id)
            entry = self._find_session_entry(index, session_id)
        shard_id = entry["shard_id"]
        data = self._read_session(user_id, shard_id, session_id)

        if is_pending:
            data["pending_events"] = _truncate_result_rows(events)
            data["pending_user_query"] = user_query
        else:
            merged: List[Dict[str, Any]] = []
            merged.extend(data.get("pending_events", []))
            merged.extend(_truncate_result_rows(events))
            # user_query 优先取 query 阶段暂存的（resume 场景 body.query 可能为空）
            uq = data.get("pending_user_query") or user_query
            turn_index = len(data["turns"]) + 1
            data["turns"].append({
                "turn_index": turn_index,
                "timestamp": _now_iso(),
                "events": merged,
                "user_query": uq,
            })
            data["pending_events"] = []
            data["pending_user_query"] = ""
            # 更新 index 摘要（turn_count / updated_at）
            entry["turn_count"] = len(data["turns"])
            entry["updated_at"] = _now_iso()
            self._write_index(user_id, index)

        self._write_session(user_id, shard_id, session_id, data)

    def clear_pending(self, user_id: str, session_id: str) -> None:
        """清除会话的 pending_events（新查询开始时放弃旧反问暂存）。

        前端语义保证 awaiting_clarification 时只能 resume，但保险起见新 query（非 resume）
        开始时调用，避免残留 pending 误合并到新 turn。
        """
        index = self._read_index(user_id)
        entry = self._find_session_entry(index, session_id)
        if entry is None:
            return
        data = self._read_session(user_id, entry["shard_id"], session_id)
        if data.get("pending_events"):
            data["pending_events"] = []
            self._write_session(user_id, entry["shard_id"], session_id, data)

    def list_sessions_paged(
        self, user_id: str, page: int = 0, size: int = SHARD_SIZE
    ) -> Dict[str, Any]:
        """按 created_at 全局排序后滑动窗口分页返回会话摘要。

        Returns: { page, size, has_more, sessions }

        注意：此方法不依赖 shard 分片边界——无论新 shard 是否刚创建，
        page=0 始终返回 index 中时间最近的 ≤N 个会话（N≤size）。
        shard 写入规则不变（每 shard ≤20，满则开新 shard），仅读取逻辑变更。
        """
        index = self._read_index(user_id)
        sessions = index.get("sessions", [])
        total = len(sessions)
        # 全局按 created_at 倒序（已存在的 session 基本有序，Timsort O(n)）
        all_sorted = sorted(sessions, key=lambda s: s.get("created_at", ""), reverse=True)
        start = page * size
        if start >= total:
            return {"page": page, "size": size, "has_more": False, "sessions": []}
        end = min(start + size, total)
        return {
            "page": page,
            "size": size,
            "has_more": end < total,
            "sessions": all_sorted[start:end],
        }

    def get_session_events(self, user_id: str, session_id: str) -> Optional[Dict[str, Any]]:
        """读取单会话事件流。无记录返回 None（调用方回落 session_memory 摘要）。

        Returns: { session_id, turns: [{turn_index, events}], has_events }
        """
        index = self._read_index(user_id)
        entry = self._find_session_entry(index, session_id)
        if entry is None:
            return None
        data = self._read_session(user_id, entry["shard_id"], session_id)
        turns = data.get("turns", [])
        return {
            "session_id": session_id,
            "turns": turns,
            "has_events": len(turns) > 0,
        }

    def delete_session(self, user_id: str, session_id: str) -> bool:
        """删除会话：index 移除 + 删 shard 文件。"""
        index = self._read_index(user_id)
        entry = self._find_session_entry(index, session_id)
        if entry is None:
            return False
        shard_id = entry["shard_id"]
        index["sessions"] = [s for s in index["sessions"] if s.get("session_id") != session_id]
        self._write_index(user_id, index)
        f = self._session_file(user_id, shard_id, session_id)
        try:
            if f.exists():
                f.unlink()
        except OSError as e:
            logger.warning(f"删除 event_cache 会话文件失败 ({f}): {e}")
        return True

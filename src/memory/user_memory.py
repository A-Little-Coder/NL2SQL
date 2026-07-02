"""
用户长期记忆（UserMemory）

六维记忆结构：
  - term_preferences:       术语偏好（用户澄清/主动教的映射）
  - frequently_used_tables: 常用表（自动学习）
  - metric_definitions:     指标定义（auto_learned + user_taught 双轨）
  - query_preferences:      查询偏好（自动学习时间/排序/limit）
  - domain_context:         领域上下文（行业/部门/关注领域）
  - clarification_history:  反问澄清历史

隐私声明：
  # TODO: 待生产化时补充隐私层（加密存储/脱敏）
  本期内容以明文 JSON 存储，不做任何脱敏处理。
"""

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from .storage import Storage


ALLOWED_TOPICS = {
    "term_preferences",
    "frequently_used_tables",
    "metric_definitions",
    "query_preferences",
    "domain_context",
    "clarification_history",
}

_BLOCKED_MEMORY_KEYS = {
    "few_shot",
    "few_shots",
    "few_shot_examples",
    "examples",
    "sql_examples",
    "final_result",
    "result",
    "result_rows",
    "execution_results",
    "llm_thinking",
    "graph_state",
    "intermediate_state",
}

class UserMemory:
    """用户长期记忆，管理六维记忆结构的读写"""

    def __init__(self, user_id: str, base_dir: str = "data/user_memory"):
        self.user_id = user_id
        self._storage = Storage(base_dir)
        self._file_path = self._storage.user_path(user_id)
        self._data: Dict[str, Any] = {}
        self._loaded = False

    # ── 内部结构 ──────────────────────────────────────────────

    @staticmethod
    def _empty_memory(user_id: str) -> Dict[str, Any]:
        return {
            "user_id": user_id,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "term_preferences": {},
            "frequently_used_tables": {},
            "metric_definitions": {},
            "query_preferences": {},
            "domain_context": {},
            "clarification_history": [],
        }

    @staticmethod
    def _normalize_memory(user_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """规范化为固定 UserMemory topic schema，过滤未知顶层 key"""
        base = UserMemory._empty_memory(user_id)
        if not isinstance(data, dict):
            return base
        for key in ("created_at", "updated_at"):
            if data.get(key):
                base[key] = data[key]
        for topic in ALLOWED_TOPICS:
            if topic in data and isinstance(data[topic], type(base[topic])):
                base[topic] = data[topic]
        return base

    @staticmethod
    def _sanitize_mapping(data: Dict[str, Any]) -> Dict[str, Any]:
        """过滤 few-shot、结果数据和中间状态等不应进入长期记忆的字段"""
        if not isinstance(data, dict):
            return {}
        return {
            k: v for k, v in data.items()
            if k not in _BLOCKED_MEMORY_KEYS
        }

    def _touch(self):
        self._data["updated_at"] = datetime.now().isoformat(timespec="seconds")

    def _ensure_loaded(self):
        if not self._loaded:
            self.load()

    # ── 加载与保存 ────────────────────────────────────────────

    def load(self) -> Dict[str, Any]:
        """加载用户记忆，文件不存在时自动创建初始结构"""
        data = self._storage.atomic_read(self._file_path)
        if data is None:
            data = self._empty_memory(self.user_id)
            self._storage.atomic_write(self._file_path, data)
        data = self._normalize_memory(self.user_id, data)
        self._data = data
        self._loaded = True
        return self._data

    def save(self):
        """原子写入用户记忆到磁盘"""
        self._data = self._normalize_memory(self.user_id, self._data)
        self._touch()
        self._storage.atomic_write(self._file_path, self._data)

    # ── 1. 术语偏好 ───────────────────────────────────────────

    def get_term_preference(self, term: str) -> Optional[Dict[str, Any]]:
        self._ensure_loaded()
        return self._data["term_preferences"].get(term)

    def record_term_preference(
        self,
        term: str,
        resolved_to: str,
        confidence: float,
        source: str = "user_taught",
    ):
        self._ensure_loaded()
        entry = self._sanitize_mapping({
            "resolved_to": resolved_to,
            "confidence": confidence,
            "source": source,
            "last_used": date.today().isoformat(),
        })
        self._data["term_preferences"][term] = entry
        self.save()

    # ── 2. 常用表 ─────────────────────────────────────────────

    def record_table_usage(self, table_name: str):
        """记录某张表被使用了一次"""
        self._ensure_loaded()
        tables = self._data["frequently_used_tables"]
        if table_name in tables:
            tables[table_name]["query_count"] += 1
            tables[table_name]["last_used"] = date.today().isoformat()
        else:
            tables[table_name] = {
                "query_count": 1,
                "last_used": date.today().isoformat(),
            }
        self.save()

    def get_frequently_used_tables(self, top_k: int = 5) -> List[str]:
        """获取最常用的 top_k 张表"""
        self._ensure_loaded()
        tables = self._data["frequently_used_tables"]
        sorted_tables = sorted(
            tables.items(), key=lambda x: x[1]["query_count"], reverse=True
        )
        return [t[0] for t in sorted_tables[:top_k]]

    # ── 3. 指标定义 ───────────────────────────────────────────

    def record_metric_definition(
        self,
        name: str,
        description: str,
        sql_pattern: str,
        source: str = "auto_learned",
        confidence: float = 0.5,
    ):
        """
        记录指标定义

        双轨制：
          - auto_learned: 自动从 SQL 中学习，confidence 从 0.5 起递增
          - user_taught: 用户主动教，confidence=0.95，且不会被 auto_learned 覆盖
        """
        self._ensure_loaded()
        metrics = self._data["metric_definitions"]

        # 如果已存在 user_taught 条目，auto_learned 不覆盖
        if name in metrics and metrics[name].get("source") == "user_taught":
            if source == "auto_learned":
                return

        if name in metrics and source == "auto_learned":
            old = metrics[name]
            old["times_used"] = old.get("times_used", 0) + 1
            old["confidence"] = min(old.get("confidence", 0.5) + 0.1, 0.9)
            old["last_used"] = date.today().isoformat()
            # 更新 sql_pattern 如果更完整
            if len(sql_pattern) > len(old.get("sql_pattern", "")):
                old["sql_pattern"] = sql_pattern
        else:
            metrics[name] = self._sanitize_mapping({
                "description": description,
                "sql_pattern": sql_pattern,
                "source": source,
                "confidence": confidence,
                "times_used": 1,
                "last_used": date.today().isoformat(),
            })
        self.save()

    def get_metric_definitions(self, min_confidence: float = 0.7) -> List[Dict[str, Any]]:
        """获取 confidence >= min_confidence 的指标列表"""
        self._ensure_loaded()
        result = []
        for name, metric in self._data["metric_definitions"].items():
            clean = self._sanitize_mapping(metric)
            if clean.get("confidence", 0) >= min_confidence:
                result.append({"name": name, **clean})
        return result

    # ── 4. 查询偏好 ───────────────────────────────────────────

    def update_query_preference(self, key: str, value: str):
        """更新查询偏好（如 default_time_range = "last_30_days"）"""
        self._ensure_loaded()
        if key in _BLOCKED_MEMORY_KEYS:
            return
        self._data["query_preferences"][key] = value
        self.save()

    def get_query_preferences(self) -> Dict[str, Any]:
        """获取完整查询偏好字典"""
        self._ensure_loaded()
        return dict(self._data["query_preferences"])

    # ── 5. 领域上下文 ─────────────────────────────────────────

    def update_domain_context(self, **kwargs):
        """更新领域上下文（industry/department/focus_areas 等）"""
        self._ensure_loaded()
        self._data["domain_context"].update(self._sanitize_mapping(kwargs))
        self.save()

    def get_domain_context(self) -> Dict[str, Any]:
        """获取完整领域信息"""
        self._ensure_loaded()
        return dict(self._data["domain_context"])

    # ── 6. 澄清历史 ───────────────────────────────────────────

    def append_clarification(self, entry: Dict[str, Any]):
        """追加一条反问澄清历史"""
        self._ensure_loaded()
        entry = self._sanitize_mapping(entry)
        if "timestamp" not in entry:
            entry["timestamp"] = datetime.now().isoformat(timespec="seconds")
        self._data["clarification_history"].append(entry)
        self.save()

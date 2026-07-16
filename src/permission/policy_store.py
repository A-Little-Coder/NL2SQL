"""PolicyStore: 表/字段权限策略存储与查询

职责：
- 加载 deny_rules（黑名单），按 (db_id, user_id) 计算有效黑名单（多角色并集）；
- 判断字段 (table, column) 是否无权限（含表级展开）；
- 提供表级/字段级裁剪辅助（denied_columns）。

设计要点：
- 黑名单只禁不放开，多角色并集即最严；
- 表级规则（column_pattern=None）在 is_denied 中自动覆盖该表所有列；
- 通配用 fnmatch，匹配前统一小写以忽略大小写差异；
- 调用方应先 get_effective_deny 取一次规则集，再用 is_denied 遍历，避免逐字段查库。
"""

import fnmatch
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from loguru import logger

from src.permission.models import DenyRule


def _default_db_path() -> str:
    """auth/table_field_acl.db 默认路径（项目根 auth/ 下）"""
    return str(Path(__file__).parent.parent.parent / "auth" / "table_field_acl.db")


class PolicyStore:
    """权限策略存储与查询"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or _default_db_path()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    # ── 查询 ───────────────────────────────────────────────────

    def get_role_ids(self, user_id: str) -> List[str]:
        """用户绑定的所有角色 id"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role_id FROM user_roles WHERE user_id=?", (user_id,)
            ).fetchall()
        return [r[0] for r in rows]

    def get_deny_rules(self, db_id: str, role_ids: List[str]) -> List[DenyRule]:
        """指定库 + 一组角色的黑名单规则"""
        if not role_ids:
            return []
        placeholders = ",".join("?" * len(role_ids))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, db_id, role_id, table_pattern, column_pattern, reason "
                f"FROM deny_rules WHERE db_id=? AND role_id IN ({placeholders})",
                [db_id] + list(role_ids),
            ).fetchall()
        return [
            DenyRule(
                id=r[0], db_id=r[1], role_id=r[2],
                table_pattern=r[3], column_pattern=r[4], reason=r[5],
            )
            for r in rows
        ]

    def get_effective_deny(self, db_id: str, user_id: str) -> List[DenyRule]:
        """有效黑名单 = 用户所有角色的 deny_rules 并集（黑名单只禁不放开，并集即最严）"""
        role_ids = self.get_role_ids(user_id)
        rules = self.get_deny_rules(db_id, role_ids)
        logger.debug(
            f"[PolicyStore] user={user_id} db={db_id} roles={role_ids} deny_rules={len(rules)}"
        )
        return rules

    # ── 判断 ───────────────────────────────────────────────────

    @staticmethod
    def is_denied(rules: List[DenyRule], table: str, column: str) -> bool:
        """字段 (table, column) 是否被禁（含表级展开）

        被禁 ⟺ 存在规则 table_pattern 匹配 table 且
                (column_pattern 为 None【整表禁】 或 column_pattern 匹配 column)
        """
        t_lower = (table or "").lower()
        c_lower = (column or "").lower()
        for r in rules:
            if not fnmatch.fnmatch(t_lower, r.table_pattern.lower()):
                continue
            if r.column_pattern is None:
                return True  # 整表禁
            if fnmatch.fnmatch(c_lower, r.column_pattern.lower()):
                return True
        return False

    @staticmethod
    def is_table_denied(rules: List[DenyRule], table: str) -> bool:
        """整表是否被禁（存在表级规则 column_pattern=None 匹配该表）"""
        t_lower = (table or "").lower()
        for r in rules:
            if r.column_pattern is None and fnmatch.fnmatch(t_lower, r.table_pattern.lower()):
                return True
        return False

    @staticmethod
    def denied_columns(
        rules: List[DenyRule], table: str, all_columns: List[str]
    ) -> Set[str]:
        """某表的无权限列集合。表级禁则返回全部列。"""
        if PolicyStore.is_table_denied(rules, table):
            return set(all_columns)
        result: Set[str] = set()
        for col in all_columns:
            if PolicyStore.is_denied(rules, table, col):
                result.add(col)
        return result

    # ── 写操作（管理后台 CRUD）──────────────────────────────

    def add_role(self, role_id: str, name: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO roles(role_id, name) VALUES(?,?)",
                (role_id, name),
            )
            conn.commit()

    def list_roles(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT role_id, name FROM roles").fetchall()
        return [{"role_id": r[0], "name": r[1]} for r in rows]

    def add_user(self, user_id: str, name: str, dept: Optional[str] = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO users(user_id, name, dept) VALUES(?,?,?)",
                (user_id, name, dept),
            )
            conn.commit()

    def list_users(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT user_id, name, dept FROM users").fetchall()
        return [{"user_id": r[0], "name": r[1], "dept": r[2]} for r in rows]

    def bind_user_role(self, user_id: str, role_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO user_roles(user_id, role_id) VALUES(?,?)",
                (user_id, role_id),
            )
            conn.commit()

    def list_user_roles(self, user_id: str) -> List[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role_id FROM user_roles WHERE user_id=?", (user_id,)
            ).fetchall()
        return [r[0] for r in rows]

    def add_deny_rule(
        self, db_id: str, role_id: str, table_pattern: str,
        column_pattern: Optional[str] = None, reason: Optional[str] = None,
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO deny_rules(db_id, role_id, table_pattern, column_pattern, reason) "
                "VALUES(?,?,?,?,?)",
                (db_id, role_id, table_pattern, column_pattern, reason),
            )
            conn.commit()
            return cur.lastrowid

    def list_deny_rules(
        self, db_id: Optional[str] = None, role_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        sql = "SELECT id, db_id, role_id, table_pattern, column_pattern, reason FROM deny_rules WHERE 1=1"
        params: List[Any] = []
        if db_id:
            sql += " AND db_id=?"
            params.append(db_id)
        if role_id:
            sql += " AND role_id=?"
            params.append(role_id)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            {"id": r[0], "db_id": r[1], "role_id": r[2], "table_pattern": r[3],
             "column_pattern": r[4], "reason": r[5]}
            for r in rows
        ]

    def delete_deny_rule(self, rule_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM deny_rules WHERE id=?", (rule_id,))
            conn.commit()

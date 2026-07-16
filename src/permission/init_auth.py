"""auth/table_field_acl.db 初始化与种子数据

运行: python -m src.permission.init_auth
"""

import sqlite3
from pathlib import Path
from typing import Optional

from loguru import logger


def _default_db_path() -> str:
    """auth/table_field_acl.db 默认路径（项目根 auth/ 下）"""
    return str(Path(__file__).parent.parent.parent / "auth" / "table_field_acl.db")


SCHEMA = """
CREATE TABLE IF NOT EXISTS roles (
    role_id TEXT PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    dept TEXT
);
CREATE TABLE IF NOT EXISTS user_roles (
    user_id TEXT NOT NULL,
    role_id TEXT NOT NULL,
    PRIMARY KEY (user_id, role_id)
);
CREATE TABLE IF NOT EXISTS deny_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    db_id TEXT NOT NULL,
    role_id TEXT NOT NULL,
    table_pattern TEXT NOT NULL,
    column_pattern TEXT,
    reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_deny_db_role ON deny_rules(db_id, role_id);
"""


def init_db(db_path: Optional[str] = None) -> str:
    """创建 auth 元数据库表结构（幂等）"""
    db_path = db_path or _default_db_path()
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()
    logger.info(f"auth 表结构已初始化: {db_path}")
    return db_path


def seed_demo(db_path: Optional[str] = None) -> None:
    """演示种子数据：角色 / 员工 / 角色绑定 / 黑名单（db_id=california_schools）

    黑名单规则用通配表达，不依赖具体表是否存在；实际字段以库为准，可在后台调整。
    """
    db_path = db_path or _default_db_path()
    with sqlite3.connect(db_path) as conn:
        c = conn.cursor()
        c.executemany(
            "INSERT OR IGNORE INTO roles(role_id, name) VALUES(?,?)",
            [("staff", "普通员工"), ("manager", "管理者"), ("admin", "管理员")],
        )
        c.executemany(
            "INSERT OR IGNORE INTO users(user_id, name, dept) VALUES(?,?,?)",
            [
                ("u_alice", "Alice", "销售部"),
                ("u_bob", "Bob", "销售部"),
                ("u_carol", "Carol", "人事部"),
            ],
        )
        c.executemany(
            "INSERT OR IGNORE INTO user_roles(user_id, role_id) VALUES(?,?)",
            [("u_alice", "staff"), ("u_bob", "manager"), ("u_carol", "admin")],
        )
        # 黑名单（演示用，db_id=california_schools；通配列不依赖具体表存在）
        c.executemany(
            "INSERT INTO deny_rules(db_id, role_id, table_pattern, column_pattern, reason) "
            "VALUES(?,?,?,?,?)",
            [
                ("california_schools", "staff", "*", "Latitude", "普通员工不可查经纬度"),
                ("california_schools", "staff", "*", "Longitude", "普通员工不可查经纬度"),
                ("california_schools", "staff", "*", "Phone", "普通员工不可查联系方式"),
                ("california_schools", "manager", "*", "Phone", "管理者不可查联系方式"),
                ("california_schools", "staff", "schools", "Street", "普通员工不可查学校街道"),
            ],
        )
        conn.commit()
    logger.info("auth 种子数据已写入")


def main() -> None:
    db_path = init_db()
    seed_demo(db_path)


if __name__ == "__main__":
    main()

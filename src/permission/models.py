"""权限模型数据结构"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class Role:
    """角色"""

    role_id: str
    name: str


@dataclass
class User:
    """员工"""

    user_id: str
    name: str
    dept: Optional[str] = None


@dataclass
class DenyRule:
    """黑名单规则：禁止访问的表/字段

    - column_pattern 为 None 表示整表禁；
    - table_pattern / column_pattern 支持 fnmatch 通配（* 匹配任意，? 匹配单字符）；
    - db_id 适配多库架构，策略 per-(db_id, role)。
    """

    db_id: str
    role_id: str
    table_pattern: str
    column_pattern: Optional[str] = None  # None = 整表禁
    reason: Optional[str] = None
    id: Optional[int] = None

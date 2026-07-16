"""表/字段权限管理模块

黑名单模型：deny_rules 存禁止访问的表/字段，其余默认放行。
多角色取并集；表级规则（column_pattern=None）展开为该表所有字段禁。
通配采用 fnmatch 风格（* 匹配任意）。
"""

from src.permission.models import DenyRule, Role, User
from src.permission.policy_store import PolicyStore

__all__ = ["DenyRule", "Role", "User", "PolicyStore"]

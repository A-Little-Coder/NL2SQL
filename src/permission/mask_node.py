"""脱敏节点（decision 之后，END 之前）

解析 final_sql 涉及字段，对 final_result 中黑名单命中列统一替换为 ***。
覆盖主路径与 cache_hit 路径，不依赖权限节点传递标记（基于 final_sql 自判）。
聚合字段无权限则聚合结果列脱敏（C）。

环境变量 TABLE_FIELD_ACL_ENABLED 控制开关（默认关，关时直通）。
"""

import os
from typing import Any, Dict

from loguru import logger

from src.permission.policy_store import PolicyStore
from src.permission.sql_parser import parse_select_outputs

_MASK_VALUE = "***"


def _is_acl_enabled() -> bool:
    return os.getenv("TABLE_FIELD_ACL_ENABLED", "false").lower() == "true"


def _mask_fallback_by_name(state, final_result, rules, qid):
    """SQL 解析失败时的保守脱敏（5.4）：结果列名命中黑名单字段名即脱敏"""
    denied_names = {
        r.column_pattern.lower()
        for r in rules
        if r.column_pattern and "*" not in r.column_pattern
    }
    if not denied_names or not isinstance(final_result, list):
        return {}
    masked_keys = set()
    new_result = []
    for row in final_result:
        if isinstance(row, dict):
            new_row = {}
            for k, v in row.items():
                k_lower = k.lower()
                if any(d in k_lower or k_lower in d for d in denied_names):
                    new_row[k] = _MASK_VALUE
                    masked_keys.add(k)
                else:
                    new_row[k] = v
            new_result.append(new_row)
        else:
            new_result.append(row)
    if not masked_keys:
        return {}
    logger.info(f"[qid={qid}] [Mask] 解析失败兜底脱敏列={list(masked_keys)}")
    return {
        "final_result": new_result,
        "trace_log": state.get("trace_log", []) + [f"[Mask] 兜底脱敏: {list(masked_keys)}"],
    }


def make_mask_node(policy_store: PolicyStore):
    """构造脱敏节点

    Args:
        policy_store: PolicyStore 实例
    """

    def node(state: Dict[str, Any]) -> Dict[str, Any]:
        qid = state.get("query_id", "")

        if not _is_acl_enabled() or policy_store is None:
            return {}

        final_sql = state.get("final_sql", "")
        final_result = state.get("final_result")
        if not final_sql or not final_result:
            return {}

        user_id = state.get("user_id", "default")
        db_id = state.get("database_filter") or ""
        rules = policy_store.get_effective_deny(db_id, user_id)
        if not rules:
            return {}

        # 解析 final_sql，找需脱敏的输出列（按位置对齐结果列，避免 alias 文本不一致）
        outputs = parse_select_outputs(final_sql)
        if not outputs:
            # 解析失败兜底（5.4）：对结果列名做黑名单字段名字符串匹配，命中即脱敏
            return _mask_fallback_by_name(state, final_result, rules, qid)
        is_denied = lambda t, c: PolicyStore.is_denied(rules, t, c)
        masked_idx = [
            i for i, o in enumerate(outputs)
            if o["refs"] and any(is_denied(t, c) for t, c in o["refs"])
        ]
        if not masked_idx:
            return {}

        masked_set_idx = set(masked_idx)

        # 对 final_result 每行按位置脱敏（第 i 个 output 对应 dict 第 i 个 key）
        if isinstance(final_result, list):
            new_result = []
            for row in final_result:
                if isinstance(row, dict):
                    keys = list(row.keys())
                    new_row = dict(row)
                    for i in masked_set_idx:
                        if i < len(keys):
                            new_row[keys[i]] = _MASK_VALUE
                    new_result.append(new_row)
                else:
                    new_result.append(row)
            logger.info(
                f"[qid={qid}] [Mask] 脱敏列索引={masked_idx} 行数={len(new_result)}"
            )
            return {
                "final_result": new_result,
                "trace_log": state.get("trace_log", [])
                + [f"[Mask] 脱敏列索引: {masked_idx}"],
            }
        return {}

    return node

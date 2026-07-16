"""权限检查节点（SS 之后，schema_finalize 之前）

按关键词分组判断（D3，task 11.1 修正）：
1. 回捞 retrieved_context.keyword_columns_map（phrase -> ["table.column"]），与
   selected_schema 字段取交集，逐关键词分组；
2. 对每个关键词组 S（S = 该关键词召回字段 ∩ selected_schema）：
   - S = ∅（SS 后该关键词无字段进入 schema）-> 跳过，不参与全无权判断（SS 已判定无关）；
   - S 全部无权 -> 该关键词为「全无权关键词」，其字段标记 keep（保留供脱敏，不剔除）；
   - S 部分无权 -> 无权字段标记 prune（剔除），有权字段保留；
   - S 全有权 -> 无操作。
   注：同一字段被多个关键词召回时，keep 优先于 prune（全无权语义优先）。
3. 不在任何关键词组内的 selected_schema 字段若无权 -> 标记 prune（剔除），但不触发全无权（无关键词背书）。
4. 查询级聚合（核心语义）：任一关键词全无权（存在 keep 字段）-> interrupt 反问 permission_choice；
   - mask：keep 字段保留参与生成（末尾脱敏节点脱敏），prune 字段仍剔除；
   - reject：设 rejection_reason，条件边路由 END 拒答。
5. 无全无权关键词 -> 部分剔除：prune 所有无权字段，acl_removed_fields 供 trace_log/前台展示"拦截字段"。

环境变量 TABLE_FIELD_ACL_ENABLED 控制开关（默认关，关时直通）。
"""

import os
from typing import Any, Dict, List

from loguru import logger

try:
    from langgraph.types import interrupt
except Exception:  # pragma: no cover
    interrupt = None  # type: ignore

from src.permission.policy_store import PolicyStore
from src.schema_selection.schema_selector import MSchemaTable

# 流式 SSE（权限拦截字段透传前台展示）；无 API 模块时退化为静默
try:
    from src.api.streaming import emit_safe
except Exception:  # pragma: no cover
    def emit_safe(event_type, data):  # type: ignore
        return


def _is_acl_enabled() -> bool:
    return os.getenv("TABLE_FIELD_ACL_ENABLED", "false").lower() == "true"


def _analyze(schema: List[MSchemaTable], kcm: Dict[str, List[str]], rules):
    """按关键词分组分析权限（纯函数，便于单测）。

    Args:
        schema: selected_schema（SS 精选）
        kcm: retrieved_context.keyword_columns_map，phrase -> ["table.column"]
        rules: 有效黑名单 deny_rules

    Returns:
        dict:
        - keep_fields: List[str]  全无权关键词的字段（"table.column"，保留供脱敏）
        - prune_fields: List[str] 部分无权/无关键词背书的剔除字段（"table.column"）
        - has_full_deny: bool     是否存在任一全无权关键词
    """
    # selected 字段索引：lower("table.column") -> (table_name, col_name)
    selected_lower: Dict[str, tuple] = {}
    for table in schema:
        for col in table.columns:
            selected_lower[f"{table.name}.{col.name}".lower()] = (table.name, col.name)

    denied_status: Dict[str, str] = {}  # key_lower -> "keep" | "prune"
    keyword_claimed: set = set()

    for phrase, col_keys in (kcm or {}).items():
        # S = 该关键词召回字段 ∩ selected_schema
        s_items = []  # [(key_lower, (table, col))]
        for k in col_keys:
            kl = k.lower()
            tc = selected_lower.get(kl)
            if tc is not None:
                s_items.append((kl, tc))
                keyword_claimed.add(kl)
        if not s_items:
            continue  # S = ∅，跳过（SS 已判定该关键词无关）
        denied_in_s = [(kl, tc) for kl, tc in s_items
                       if PolicyStore.is_denied(rules, tc[0], tc[1])]
        if not denied_in_s:
            continue  # 全有权
        if len(denied_in_s) == len(s_items):
            # 全无权关键词：其字段 keep（保留供脱敏）；keep 优先于 prune
            for kl, _ in denied_in_s:
                denied_status[kl] = "keep"
        else:
            # 部分无权：无权字段 prune（若未被 keep 占据）
            for kl, _ in denied_in_s:
                if denied_status.get(kl) != "keep":
                    denied_status[kl] = "prune"

    # 不在任何关键词组内的无权字段 -> prune（无关键词背书，不触发全无权）
    for kl, (table, col) in selected_lower.items():
        if kl in keyword_claimed:
            continue
        if PolicyStore.is_denied(rules, table, col):
            if kl not in denied_status:
                denied_status[kl] = "prune"

    keep_fields: List[str] = []
    prune_fields: List[str] = []
    for kl, (table, col) in selected_lower.items():
        status = denied_status.get(kl)
        if status == "keep":
            keep_fields.append(f"{table}.{col}")
        elif status == "prune":
            prune_fields.append(f"{table}.{col}")

    return {
        "keep_fields": keep_fields,
        "prune_fields": prune_fields,
        "has_full_deny": bool(keep_fields),
    }


def _prune_schema(schema: List[MSchemaTable], remove_keys_lower: set) -> List[MSchemaTable]:
    """从 schema 剔除 remove_keys_lower 中的字段（lower "table.column"），空表移除。

    仅剔除指定字段（不按表级黑名单整表跳过），保证全无权 keep 字段不被误删。
    """
    new_schema: List[MSchemaTable] = []
    for table in schema:
        kept = [c for c in table.columns
                if f"{table.name}.{c.name}".lower() not in remove_keys_lower]
        if kept:
            new_schema.append(
                MSchemaTable(
                    name=table.name,
                    columns=kept,
                    description=table.description,
                    row_count=table.row_count,
                )
            )
    return new_schema


def make_permission_node(policy_store: PolicyStore):
    """构造权限检查节点

    Args:
        policy_store: PolicyStore 实例（全局，deny_rules 带 db_id 维度）
    """

    def node(state: Dict[str, Any]) -> Dict[str, Any]:
        qid = state.get("query_id", "")

        # flag 关闭或无 store：直通
        if not _is_acl_enabled() or policy_store is None:
            return {}

        user_id = state.get("user_id", "default")
        db_id = state.get("database_filter") or ""
        schema: List[MSchemaTable] = state.get("selected_schema", []) or []

        if not schema:
            return {}

        rules = policy_store.get_effective_deny(db_id, user_id)
        if not rules:
            return {}  # 无黑名单，全放行

        # 回捞关键词->字段映射
        ctx = state.get("retrieved_context")
        kcm = getattr(ctx, "keyword_columns_map", None) or {}

        analysis = _analyze(schema, kcm, rules)
        keep_fields = analysis["keep_fields"]
        prune_fields = analysis["prune_fields"]
        has_full_deny = analysis["has_full_deny"]

        # ---- 任一关键词全无权 -> interrupt 反问 ----
        if has_full_deny:
            fields_text = "、".join(keep_fields)
            # 多意图路径降级：invoke 不支持 interrupt，直接拒答该子查询
            if state.get("_multi_intent"):
                reason = f"以下字段无访问权限：{fields_text}"
                logger.info(
                    f"[qid={qid}] [Permission] 多意图降级拒答: {keep_fields}"
                )
                return {
                    "rejection_reason": reason,
                    "selected_schema": [],
                    "trace_log": state.get("trace_log", [])
                    + [f"[Permission] 多意图降级拒答: {reason}"],
                }
            question = (
                f"您查询的字段无访问权限：{fields_text}\n"
                f"是否以脱敏方式（字段值替换为 ***）继续？"
            )
            payload = {
                "question": question,
                "ambiguities": [],
                "round": 1,
                "kind": "permission_choice",
                "options": [
                    {"label": "脱敏继续", "value": "mask"},
                    {"label": "放弃", "value": "reject"},
                ],
            }
            logger.info(
                f"[qid={qid}] [Permission] 全无权反问: {keep_fields} "
                f"(同时部分剔除: {prune_fields})"
            )
            if interrupt is None:  # pragma: no cover
                return {
                    "rejection_reason": f"以下字段无访问权限：{fields_text}",
                    "selected_schema": [],
                }
            choice = interrupt(payload)
            if str(choice).strip() == "reject":
                reason = f"以下字段无访问权限：{fields_text}"
                emit_safe("permission", {
                    "action": "full_deny_reject",
                    "removed_fields": [],
                    "keep_fields": keep_fields,
                })
                return {
                    "rejection_reason": reason,
                    "selected_schema": [],
                    "trace_log": state.get("trace_log", [])
                    + [f"[Permission] 放弃: {reason}"],
                }
            # mask：keep 字段保留参与生成（不剔除，末尾脱敏节点处理）；
            # prune 字段（部分无权）仍剔除；acl_removed_fields 仅含 prune 字段
            out: Dict[str, Any] = {
                "trace_log": state.get("trace_log", [])
                + [f"[Permission] 脱敏继续: keep={keep_fields} prune={prune_fields}"]
            }
            if prune_fields:
                out["selected_schema"] = _prune_schema(
                    schema, {f.lower() for f in prune_fields}
                )
                out["acl_removed_fields"] = prune_fields
            emit_safe("permission", {
                "action": "full_deny_mask",
                "removed_fields": prune_fields,
                "keep_fields": keep_fields,
            })
            return out

        # ---- 无全无权关键词：部分剔除 ----
        if not prune_fields:
            emit_safe("permission", {"action": "pass", "removed_fields": [], "keep_fields": []})
            return {}  # 无任何无权字段，直通
        new_schema = _prune_schema(schema, {f.lower() for f in prune_fields})
        logger.info(
            f"[qid={qid}] [Permission] 部分剔除: {len(schema)}->{len(new_schema)} 表, "
            f"removed={prune_fields}"
        )
        emit_safe("permission", {
            "action": "partial_prune",
            "removed_fields": prune_fields,
            "keep_fields": [],
        })
        out = {
            "selected_schema": new_schema,
            "trace_log": state.get("trace_log", []) + ["[Permission] 部分无权剔除"],
        }
        if prune_fields:
            out["acl_removed_fields"] = prune_fields
        return out

    return node

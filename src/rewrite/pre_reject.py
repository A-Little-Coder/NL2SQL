# ============================================================================
# 前置拒答检测节点（PreReject）— v2 设计
# ============================================================================
# 功能：硬性违规检测，不调 LLM
#   1. 空查询/纯空白 → 拒答
#   2. SQL 写操作关键词（insert/update/delete/drop/create/alter/truncate 等）
#   3. 中文写操作意图词（删除/修改/更新/插入/清空/建表等）
#   4. 正常 → 放行到 Rewrite 子图
# ============================================================================

import re
from typing import Any, Dict, Optional

from src.graph.state import NL2SQLState


# ---------------------------------------------------------------------------
# 写操作关键词
# ---------------------------------------------------------------------------
WRITE_OPERATION_KEYWORDS = [
    "insert", "update", "delete", "drop", "create", "alter", "truncate",
    "replace", "merge", "grant", "revoke",
]
WRITE_OPERATION_ZH = [
    "删除", "修改", "更新", "插入", "清空", "清空表", "建表", "删除表",
    "添加数据", "改数据", "删数据",
]


def _detect_write_operation(query: str) -> bool:
    """检测查询是否包含写操作意图（SQL 关键词 + 中文写操作词）。"""
    q_lower = query.lower()
    for kw in WRITE_OPERATION_KEYWORDS:
        if re.search(r"\b" + re.escape(kw) + r"\b", q_lower):
            return True
    for kw in WRITE_OPERATION_ZH:
        if kw in query:
            return True
    return False


def make_pre_reject_node() -> callable:
    """
    构造前置拒答检测节点。

    硬性检测，不调 LLM，检测到违规直接拒答。

    Returns:
        Callable[[NL2SQLState], Dict[str, Any]]: LangGraph 节点函数
    """

    def pre_reject_node(state: NL2SQLState) -> Dict[str, Any]:
        """
        前置拒答检测主逻辑。

        输入（从 state 读取）:
            - user_query: 当前用户查询
            - trace_log: 轨迹日志

        输出（返回 dict，LangGraph 浅合并）:
            - rejection_reason: 主流程拒答原因（违规时设）
            - rewrite_rejection_reason: 前置拒答原因（违规时设）
            - trace_log: 追加的轨迹日志
        """
        user_query = state.get("user_query", "")
        trace_log = state.get("trace_log", [])[:]

        # 1. 空查询检测
        if not user_query or not user_query.strip():
            return {
                "rejection_reason": "查询为空，无法理解意图",
                "rewrite_rejection_reason": "查询为空",
                "trace_log": trace_log + ["[PreReject] 空查询拒答"],
            }

        # 2. 写操作检测
        if _detect_write_operation(user_query):
            reason = "本服务仅支持查询，不支持数据写操作（删除/修改/插入等）"
            return {
                "rejection_reason": reason,
                "rewrite_rejection_reason": "检测到写操作意图",
                "trace_log": trace_log + ["[PreReject] 写操作检测拒答"],
            }

        # 3. 正常放行
        return {
            "trace_log": trace_log + ["[PreReject] 通过"],
        }

    return pre_reject_node
# ============================================================================
# Rewrite 模块：NL 问句改写（v2 设计）
# ============================================================================
# 模块结构：
#   - pre_reject.py: 前置拒答检测节点（硬性写操作/空查询检测）
#   - prompts.py: DETECT_ISSUES_PROMPT + REWRITE_EXECUTE_PROMPT
#   - rewrite_subgraph.py: Rewrite 子图（问题检测 + 改写执行 + 反问澄清）
# ============================================================================

from src.rewrite.pre_reject import make_pre_reject_node
from src.rewrite.prompts import DETECT_ISSUES_PROMPT, REWRITE_EXECUTE_PROMPT
from src.rewrite.rewrite_subgraph import make_rewrite_node, build_rewrite_subgraph

__all__ = [
    "make_pre_reject_node",
    "make_rewrite_node",
    "build_rewrite_subgraph",
    "DETECT_ISSUES_PROMPT",
    "REWRITE_EXECUTE_PROMPT",
]
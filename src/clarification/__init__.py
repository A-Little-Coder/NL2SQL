# Clarification 模块（反问机制，决策 9-15）
#
# 模块构成：
#   - task_planner.py          意图理解 + 三选一裁决 + 多意图分解（决策 9/10）
#   - dialog.py                interrupt 包装 + 5 次硬上限 + 拒答关键词（决策 12/13）
#   - subquery_orchestrator.py 多子查询串行编排 + 失败隔离（决策 14）
#   - result_summarizer.py     总结模块：按需 LLM + 数据表结构摘要降 token（决策 15）
#   - agent.py                 反问子图组装（task_planner + dialog 组合）
#   - prompts.py               LLM Prompt 模板

from src.clarification.task_planner import TaskPlanner, PlanResult
from src.clarification.dialog import DialogManager, DECLINED, MAX_REACHED
from src.clarification.subquery_orchestrator import SubqueryOrchestrator, SubqueryResult
from src.clarification.result_summarizer import ResultSummarizer

__all__ = [
    "TaskPlanner", "PlanResult",
    "DialogManager", "DECLINED", "MAX_REACHED",
    "SubqueryOrchestrator", "SubqueryResult",
    "ResultSummarizer",
]

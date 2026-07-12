# Clarification 模块（v2 精简版）
#
# 模块构成：
#   - task_decomposer.py    意图拆解（单意图/多意图分解，v2 无反问/拒答）
#   - subquery_orchestrator.py 多子查询串行编排 + 失败隔离（决策 14）
#   - result_summarizer.py   总结模块：按需 LLM + 数据表结构摘要降 token（决策 15）
#   - prompts.py             LLM Prompt 模板

from src.clarification.task_decomposer import TaskDecomposer, PlanResult
from src.clarification.subquery_orchestrator import SubqueryOrchestrator, SubqueryResult
from src.clarification.result_summarizer import ResultSummarizer

__all__ = [
    "TaskDecomposer", "PlanResult",
    "SubqueryOrchestrator", "SubqueryResult",
    "ResultSummarizer",
]
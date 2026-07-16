# ============================================================================
# SubqueryOrchestrator：多子查询串行编排（决策 14 / refactor-single-query-graph）
# ============================================================================
# 职责：
#   把 TaskPlanner 分解出的 N 个子查询，逐个喂给编译好的 single_query_graph
#   （ir → ss → answerability_check → cg → execution → decision），收集每个的
#   final_sql / final_result / decision_path 进 subquery_results。
#
# 设计（refactor-single-query-graph）：
#   - 单一事实来源：单查询流水线胶水只存在于 single_query_graph 一处，
#     orchestrator 不再平行重写顺序 invoke 逻辑（原 run_single_query() 已删除）。
#   - 失败隔离：某子查询全失败 / 抛异常不中断其他子查询，各自带 decision_path
#     与失败原因。
#   - 串行执行：保留 Python for 循环（不改并行 Send fan-out），保留 ContextVar
#     （fix_loop/user_memory/session_memory）串行传递约束。
# ============================================================================

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from loguru import logger


@dataclass
class SubqueryResult:
    """单个子查询的执行结果

    Attributes:
        subquery: 子查询原文
        final_sql: 选定的 SQL（失败时为空）
        final_result: SQL 执行结果
        decision_path: 决策路径（A-H）；拒答为 "REJECTED"，失败为 "FAILED"
        fix_failed: SmartFix 是否失败
        error: 失败原因（成功时为空）
        success: 是否成功产出最终 SQL
    """

    subquery: str = ""
    final_sql: str = ""
    final_result: Any = None
    decision_path: str = ""
    fix_failed: bool = False
    error: str = ""
    success: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subquery": self.subquery,
            "final_sql": self.final_sql,
            "final_result": self.final_result,
            "decision_path": self.decision_path,
            "fix_failed": self.fix_failed,
            "error": self.error,
            "success": self.success,
        }


def _classify_decision_path(out: Dict[str, Any]) -> str:
    """从 single_query_graph 返回的 partial state 推断 decision_path。

    fail-fast 早退（无 schema / 不可回答 / 无候选）时 decision 节点未执行，
    decision_path 为空，需据 rejection_reason / error 回退为 REJECTED / FAILED。
    """
    dp = out.get("decision_path", "") or ""
    if dp:
        return dp
    if out.get("rejection_reason"):
        return "REJECTED"
    return "FAILED"


class SubqueryOrchestrator:
    """
    多子查询编排器（决策 14 / refactor-single-query-graph）

    串行执行每个子查询，失败隔离。

    Attributes:
        single_query_graph: 已编译的单查询流水线图（build_single_query_graph 产物），
                            主图单意图路径与本编排器共用同一编译实例。
    """

    def __init__(self, single_query_graph):
        self.graph = single_query_graph

    def run(
        self,
        subqueries: List[str],
        shared_state: Optional[Dict[str, Any]] = None,
    ) -> List[SubqueryResult]:
        """
        串行执行所有子查询

        Args:
            subqueries: 子查询列表
            shared_state: 共享上下文（conversation_history / metric_definitions /
                          database_filter / _user_memory 等，注入到每个子查询的初始 state）

        Returns:
            List[SubqueryResult]：每个子查询的结果（失败隔离，不中断）
        """
        shared_state = {**(shared_state or {}), "_multi_intent": True}  # 多意图标记：权限节点全无权直接拒答，不反问
        results: List[SubqueryResult] = []

        for idx, subq in enumerate(subqueries):
            logger.info(f"[Orchestrator] 子查询 {idx + 1}/{len(subqueries)}: {subq[:60]}")
            try:
                out = self.graph.invoke({**shared_state, "user_query": subq})
                success = bool(out.get("final_sql")) and not out.get("fix_failed", False)
                dp = _classify_decision_path(out)
                error = out.get("error", "") or ""
                if not success and not error:
                    error = f"决策路径 {dp} 未产出 SQL"
                results.append(SubqueryResult(
                    subquery=subq,
                    final_sql=out.get("final_sql", "") or "",
                    final_result=out.get("final_result"),
                    decision_path=dp,
                    fix_failed=out.get("fix_failed", False),
                    error=error,
                    success=success,
                ))
                if not success:
                    logger.warning(f"[Orchestrator] 子查询 {idx + 1} 失败: {error}")
            except Exception as e:
                # 失败隔离：记录失败但不中断后续子查询
                logger.exception(f"[Orchestrator] 子查询 {idx + 1} 异常: {e}")
                results.append(SubqueryResult(
                    subquery=subq,
                    decision_path="FAILED",
                    error=str(e),
                    success=False,
                ))

        success_count = sum(1 for r in results if r.success)
        logger.info(f"[Orchestrator] 全部完成: {success_count}/{len(results)} 成功")
        return results

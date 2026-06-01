"""
Decision Agent 子图

依据 §18.7：group_by_result → find_majority → (条件) select_fastest | llm_final_decision
"""

from typing import Any, Dict, List, TypedDict

from langgraph.graph import END, START, StateGraph


class DecisionGraphState(TypedDict, total=False):
    """Decision 子图内部状态"""

    candidates: List[Any]           # List[SQLCandidate]
    user_query: str

    groups: Dict[str, List[Any]]
    successful_groups: Dict[str, List[Any]]
    has_majority: bool
    majority_group: List[Any]
    final_decision: Any             # DecisionResult


def build_decision_graph(decider):
    """
    构建 Decision Agent 子图

    Args:
        decider: SelfConsistencyDecision 实例

    Returns:
        CompiledGraph
    """
    from src.decision.self_consistency import DecisionResult
    from src.sql_generation.sql_generator import SQLStatus

    def node_group_by_result(state: DecisionGraphState) -> Dict[str, Any]:
        cands = state.get("candidates", [])
        if not cands:
            return {
                "groups": {},
                "successful_groups": {},
                "final_decision": DecisionResult(decision_reason="无候选 SQL"),
            }
        groups = decider.group_by_result(cands)
        succ = {k: v for k, v in groups.items() if k != "__failed__"}
        return {"groups": groups, "successful_groups": succ}

    def node_find_majority(state: DecisionGraphState) -> Dict[str, Any]:
        groups = state.get("groups", {})
        has_maj, maj_key, maj_group = decider.find_majority_group(groups)
        return {"has_majority": has_maj, "majority_group": maj_group}

    def node_select_fastest(state: DecisionGraphState) -> Dict[str, Any]:
        group = state.get("majority_group", [])
        succ = state.get("successful_groups", {})
        total_succ = sum(len(v) for v in succ.values())
        best = decider.select_fastest_from_group(group)
        decision = DecisionResult(
            selected_sql=best.sql,
            selected_result=best.result,
            execution_time=best.execution_time,
            decision_reason=f"多数一致（{len(group)}/{total_succ}），选择最快",
            voting_summary={
                "total_groups": len(succ),
                "majority_size": len(group),
                "total_successful": total_succ,
            },
        )
        return {"final_decision": decision}

    def node_llm_final(state: DecisionGraphState) -> Dict[str, Any]:
        cands = state.get("candidates", [])
        succ_cands = [c for c in cands if c.status == SQLStatus.SUCCESS]
        succ_groups = state.get("successful_groups", {})
        best = decider.llm_final_decision(succ_cands, state.get("user_query", ""))
        if best is None:
            decision = DecisionResult(
                decision_reason="LLM 决策无法选择",
                voting_summary={"total_groups": len(succ_groups)},
            )
        else:
            decision = DecisionResult(
                selected_sql=best.sql,
                selected_result=best.result,
                execution_time=best.execution_time,
                decision_reason="LLM 最终决策（无多数一致）",
                voting_summary={
                    "total_groups": len(succ_groups),
                    "total_successful": len(succ_cands),
                    "llm_decided": True,
                },
            )
        return {"final_decision": decision}

    def route_after_group(state: DecisionGraphState) -> str:
        """全部失败 → END；否则进入多数判定"""
        if state.get("final_decision") is not None:
            return END
        if not state.get("successful_groups"):
            return "all_failed"
        return "find_majority"

    def node_all_failed(state: DecisionGraphState) -> Dict[str, Any]:
        groups = state.get("groups", {})
        return {
            "final_decision": DecisionResult(
                decision_reason="所有候选 SQL 执行失败",
                voting_summary={"groups": len(groups), "all_failed": True},
            )
        }

    def route_after_majority(state: DecisionGraphState) -> str:
        return "select_fastest" if state.get("has_majority") else "llm_final"

    graph = StateGraph(DecisionGraphState)
    graph.add_node("group_by_result", node_group_by_result)
    graph.add_node("find_majority", node_find_majority)
    graph.add_node("select_fastest", node_select_fastest)
    graph.add_node("llm_final", node_llm_final)
    graph.add_node("all_failed", node_all_failed)

    graph.add_edge(START, "group_by_result")
    graph.add_conditional_edges(
        "group_by_result",
        route_after_group,
        {"find_majority": "find_majority", "all_failed": "all_failed", END: END},
    )
    graph.add_conditional_edges(
        "find_majority",
        route_after_majority,
        {"select_fastest": "select_fastest", "llm_final": "llm_final"},
    )
    graph.add_edge("select_fastest", END)
    graph.add_edge("llm_final", END)
    graph.add_edge("all_failed", END)

    return graph.compile()

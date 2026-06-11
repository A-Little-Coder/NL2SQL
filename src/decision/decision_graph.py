"""Decision Agent 子图（决策 51 重写版）

新流程：
  filter_success → score_by_data (R1) → [路由]
                                          ├ 唯一=5 → verify → END (path A)
                                          ├ 并列=5 → score_by_sql (R2) → verify → END (path B/C)
                                          └ <5     → pick_for_fix → smart_fix → verify → END (path D/E)

  全失败分支：
  filter_success(空) → pick_lightest_failures → [路由]
                                                  ├ 空（全不可修） → verify → END (path H)
                                                  └ 非空 → iterate_smart_fix → verify → END (path F/G)

子图输入：candidates (List[SQLCandidate]), user_query, schema_text
子图输出：final_decision (DecisionResult)
"""

from typing import Any, Dict, List, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from loguru import logger


class DecisionGraphState(TypedDict, total=False):
    """Decision 子图内部状态（决策 51 重写）"""

    # 输入
    candidates: List[Any]            # List[SQLCandidate]
    user_query: str
    schema_text: str
    mschema: Optional[List[Any]]
    fix_loop: Optional[Any]          # SQLFixLoop 实例（决策 51；从主图节点注入）

    # 中间状态
    success_candidates: List[Any]
    failed_candidates: List[Any]
    candidate_scores_r1: List[Dict[str, Any]]
    candidate_scores_r2: Optional[List[Dict[str, Any]]]
    selected_candidate_id: Optional[str]
    selected_candidate: Optional[Any]
    is_tied_at_5: bool
    top_score: int

    # SmartFix 结果
    smart_fix_result: Optional[Dict[str, Any]]
    fix_failed: bool
    fix_rounds_used: int
    last_error: Optional[str]

    # 决策路径标识
    decision_path: str

    # 输出
    final_decision: Any              # DecisionResult


def build_decision_graph(decider):
    """构建 Decision Agent 子图（决策 51 重写）

    Args:
        decider: SelfConsistencyDecision 实例（提供 score_by_data / score_by_sql /
                 _pick_from_scores / pick_lightest_failures / llm_client / result_verifier）

    Returns:
        CompiledGraph
    """
    from src.decision.self_consistency import DecisionResult
    from src.execution.executor import SQLFixLoop
    from src.sql_generation.sql_generator import SQLStatus

    # ------------------------------------------------------------------
    # 节点
    # ------------------------------------------------------------------

    def node_filter_success(state: DecisionGraphState) -> Dict[str, Any]:
        """剔除失败候选"""
        cands = state.get("candidates", [])
        success = [c for c in cands if c.status == SQLStatus.SUCCESS]
        failed = [c for c in cands if c.status == SQLStatus.FAILED]
        return {"success_candidates": success, "failed_candidates": failed}

    def node_score_by_data(state: DecisionGraphState) -> Dict[str, Any]:
        """R1 数据视角评分"""
        scores = decider.score_by_data(
            state.get("success_candidates", []),
            state.get("user_query", ""),
        )
        return {"candidate_scores_r1": scores}

    def node_score_by_sql(state: DecisionGraphState) -> Dict[str, Any]:
        """R2 SQL 视角评分（仅在 R1 并列=5 时触发）"""
        # 取出 R1 最高分组的候选
        r1_scores = state.get("candidate_scores_r1", [])
        max_score = max((s.get("score", 0) for s in r1_scores), default=0)
        top_ids = {s["candidate_id"] for s in r1_scores if s.get("score", 0) == max_score}
        success = state.get("success_candidates", [])
        tied_cands = [c for c in success if c.id in top_ids]

        scores = decider.score_by_sql(
            tied_cands, state.get("user_query", ""), r1_scores,
        )
        return {"candidate_scores_r2": scores}

    def node_pick_for_smart_fix(state: DecisionGraphState) -> Dict[str, Any]:
        """R1<5 时挑选最高分候选送入 SmartFix"""
        scores = state.get("candidate_scores_r1", [])
        success = state.get("success_candidates", [])
        best_id, is_tied, top = decider._pick_from_scores(success, scores)
        selected = next((c for c in success if c.id == best_id), None)
        return {
            "selected_candidate_id": best_id,
            "selected_candidate": selected,
            "is_tied_at_5": False,
            "top_score": top,
        }

    def node_finalize_from_r1(state: DecisionGraphState) -> Dict[str, Any]:
        """R1 唯一最高=5：直接选定该候选（路径 A）"""
        scores = state.get("candidate_scores_r1", [])
        success = state.get("success_candidates", [])
        best_id, is_tied, top = decider._pick_from_scores(success, scores)
        selected = next((c for c in success if c.id == best_id), None)
        return {
            "selected_candidate_id": best_id,
            "selected_candidate": selected,
            "is_tied_at_5": is_tied,
            "top_score": top,
            "decision_path": "A",
        }

    def node_finalize_from_r2(state: DecisionGraphState) -> Dict[str, Any]:
        """R2 完成后挑选候选（路径 B: 唯一最高 / C: 并列选最快）"""
        r2_scores = state.get("candidate_scores_r2", []) or []
        success = state.get("success_candidates", [])
        # R2 候选池仅为 R1 并列最高的那些
        r2_ids = {s["candidate_id"] for s in r2_scores}
        r2_cands = [c for c in success if c.id in r2_ids]
        best_id, is_tied, top = decider._pick_from_scores(r2_cands, r2_scores)
        selected = next((c for c in success if c.id == best_id), None)
        path = "C" if is_tied else "B"
        return {
            "selected_candidate_id": best_id,
            "selected_candidate": selected,
            "is_tied_at_5": is_tied,
            "top_score": top,
            "decision_path": path,
        }

    def node_smart_fix_single(state: DecisionGraphState) -> Dict[str, Any]:
        """SmartFix：对选中的单个候选执行 ≤3 轮修复（路径 D/E）"""
        selected = state.get("selected_candidate")
        if selected is None:
            return {
                "smart_fix_result": None,
                "fix_failed": True,
                "fix_rounds_used": 0,
                "last_error": "no candidate selected for fix",
                "decision_path": "E",
            }

        # 决策 51：优先用子图 state 注入的 fix_loop（每 DB 独立），fallback 到 decider.fix_loop
        fix_loop = state.get("fix_loop") or getattr(decider, "fix_loop", None)
        if fix_loop is None:
            executor = getattr(decider, "executor", None)
            if executor is None:
                return {
                    "smart_fix_result": None,
                    "fix_failed": True,
                    "fix_rounds_used": 0,
                    "last_error": "no fix_loop available",
                    "decision_path": "E",
                }
            fix_loop = SQLFixLoop(executor=executor, llm_client=decider.llm_client, max_retries=3)

        initial_error = getattr(selected, "structured_error", None)
        ret = fix_loop.run(
            sql=selected.sql,
            user_query=state.get("user_query", ""),
            schema_text=state.get("schema_text", ""),
            initial_error=initial_error,
        )
        path = "D" if not ret["fix_failed"] else "E"
        return {
            "smart_fix_result": ret,
            "fix_failed": ret["fix_failed"],
            "fix_rounds_used": ret["fix_rounds_used"],
            "last_error": ret["last_error"],
            "decision_path": path,
        }

    def node_all_failed_fix(state: DecisionGraphState) -> Dict[str, Any]:
        """全失败分支：按错误等级取最轻一级，逐个 SmartFix（路径 F/G/H）"""
        failed = state.get("failed_candidates", [])
        lightest = decider.pick_lightest_failures(failed)

        # 路径 H：最轻全是不可修
        if not lightest:
            first = failed[0] if failed else None
            last_err = ""
            if first is not None:
                last_err = getattr(first, "error_message", "") or ""
            return {
                "smart_fix_result": None,
                "selected_candidate": first,
                "selected_candidate_id": first.id if first else None,
                "fix_failed": True,
                "fix_rounds_used": 0,
                "last_error": last_err,
                "decision_path": "H",
            }

        # 准备 fix_loop（优先用子图 state 注入的）
        fix_loop = state.get("fix_loop") or getattr(decider, "fix_loop", None)
        if fix_loop is None:
            executor = getattr(decider, "executor", None)
            if executor is None:
                return {
                    "smart_fix_result": None,
                    "selected_candidate": lightest[0],
                    "selected_candidate_id": lightest[0].id,
                    "fix_failed": True,
                    "fix_rounds_used": 0,
                    "last_error": "no fix_loop available",
                    "decision_path": "G",
                }
            fix_loop = SQLFixLoop(executor=executor, llm_client=decider.llm_client, max_retries=3)

        # 逐个候选尝试 SmartFix，任一成功立即返回
        last_ret = None
        last_cand = None
        for cand in lightest:
            initial_error = getattr(cand, "structured_error", None)
            ret = fix_loop.run(
                sql=cand.sql,
                user_query=state.get("user_query", ""),
                schema_text=state.get("schema_text", ""),
                initial_error=initial_error,
            )
            last_ret = ret
            last_cand = cand
            if not ret["fix_failed"]:
                # 路径 F：任一成功立即返回
                logger.info(f"全失败分支：候选 {cand.id} SmartFix 成功")
                return {
                    "smart_fix_result": ret,
                    "selected_candidate": cand,
                    "selected_candidate_id": cand.id,
                    "fix_failed": False,
                    "fix_rounds_used": ret["fix_rounds_used"],
                    "last_error": None,
                    "decision_path": "F",
                }

        # 路径 G：所有最轻候选都修不好
        return {
            "smart_fix_result": last_ret,
            "selected_candidate": last_cand,
            "selected_candidate_id": last_cand.id if last_cand else None,
            "fix_failed": True,
            "fix_rounds_used": last_ret["fix_rounds_used"] if last_ret else 0,
            "last_error": last_ret["last_error"] if last_ret else "all candidates failed",
            "decision_path": "G",
        }

    def node_assemble_decision(state: DecisionGraphState) -> Dict[str, Any]:
        """构造 DecisionResult（决策 51 扩展字段）"""
        selected = state.get("selected_candidate")
        smart_fix = state.get("smart_fix_result")
        fix_failed = state.get("fix_failed", False)
        path = state.get("decision_path", "")

        # 决定最终 SQL 和 result
        if smart_fix is not None and not fix_failed:
            # SmartFix 成功：用修复后的 SQL + 结果
            exec_result = smart_fix["result"]
            final_sql = exec_result.sql
            final_result = exec_result.result_data
            exec_time = exec_result.execution_time
        elif fix_failed:
            # SmartFix 失败：保留候选原始 SQL + 报错信息
            final_sql = selected.sql if selected else ""
            final_result = None
            exec_time = None
        else:
            # 无 SmartFix（路径 A/B/C）：用候选原始结果
            final_sql = selected.sql if selected else ""
            final_result = selected.result if selected else None
            exec_time = selected.execution_time if selected else None

        if fix_failed:
            reason = f"SmartFix 失败（路径 {path}）: {state.get('last_error', '')}"
        elif smart_fix is not None:
            reason = f"SmartFix 成功（路径 {path}，{state.get('fix_rounds_used', 0)} 轮）"
        else:
            reason = f"评分直选（路径 {path}，分数 {state.get('top_score', 0)}/5）"

        decision = DecisionResult(
            selected_sql=final_sql,
            selected_result=final_result,
            execution_time=exec_time,
            decision_reason=reason,
            voting_summary={
                "path": path,
                "top_score_r1": state.get("top_score") if path in ("A", "D", "E") else None,
                "tied_at_5": state.get("is_tied_at_5", False),
            },
            candidate_scores_r1=state.get("candidate_scores_r1", []),
            candidate_scores_r2=state.get("candidate_scores_r2"),
            selected_candidate_id=state.get("selected_candidate_id"),
            fix_failed=fix_failed,
            fix_rounds_used=state.get("fix_rounds_used", 0),
            last_error=state.get("last_error"),
            decision_path=path,
        )

        return {"final_decision": decision}

    def node_verify(state: DecisionGraphState) -> Dict[str, Any]:
        """结果可信度验证（保留决策 24，收尾节点）"""
        decision = state.get("final_decision")
        if decision is None or not decision.selected_sql:
            return {}
        verifier = getattr(decider, "result_verifier", None)
        if verifier is None:
            return {}
        try:
            verification = verifier.verify(
                user_query=state.get("user_query", ""),
                selected_sql=decision.selected_sql,
                result_sample=decision.selected_result,
                mschema=state.get("mschema") or [],
            )
            summary = decision.voting_summary or {}
            summary["verification"] = verification.to_dict()
            decision.voting_summary = summary
            if verification.should_reject:
                decision.decision_reason = (
                    (decision.decision_reason or "") + f" | 结果不可信: {verification.reason}"
                )
        except Exception as e:
            logger.warning(f"result_verifier 调用失败: {e}")
        return {"final_decision": decision}

    # ------------------------------------------------------------------
    # 路由
    # ------------------------------------------------------------------

    def route_after_filter(state: DecisionGraphState) -> str:
        if state.get("success_candidates"):
            return "score_r1"
        return "all_failed"

    def route_after_r1(state: DecisionGraphState) -> str:
        """R1 唯一=5 → finalize_r1；并列=5 → score_r2；<5 → pick_for_fix"""
        scores = state.get("candidate_scores_r1", [])
        if not scores:
            # 兜底：选第一个候选
            return "pick_for_fix"
        max_score = max(s.get("score", 0) for s in scores)
        top_ids = [s["candidate_id"] for s in scores if s.get("score", 0) == max_score]
        if max_score >= 5:
            if len(top_ids) == 1:
                return "finalize_r1"  # 路径 A
            return "score_r2"  # 路径 B/C 前置
        return "pick_for_fix"  # 路径 D/E

    def route_after_r2(state: DecisionGraphState) -> str:
        return "finalize_r2"

    def route_after_finalize_r1(state: DecisionGraphState) -> str:
        return "verify"

    def route_after_finalize_r2(state: DecisionGraphState) -> str:
        return "verify"

    def route_after_pick_for_fix(state: DecisionGraphState) -> str:
        return "smart_fix"

    def route_after_smart_fix(state: DecisionGraphState) -> str:
        return "assemble_decision"

    def route_after_all_failed(state: DecisionGraphState) -> str:
        return "assemble_decision"

    def route_after_assemble(state: DecisionGraphState) -> str:
        return "verify"

    # ------------------------------------------------------------------
    # 构图
    # ------------------------------------------------------------------

    graph = StateGraph(DecisionGraphState)
    graph.add_node("filter", node_filter_success)
    graph.add_node("score_r1", node_score_by_data)
    graph.add_node("score_r2", node_score_by_sql)
    graph.add_node("pick_for_fix", node_pick_for_smart_fix)
    graph.add_node("finalize_r1", node_finalize_from_r1)
    graph.add_node("finalize_r2", node_finalize_from_r2)
    graph.add_node("smart_fix", node_smart_fix_single)
    graph.add_node("all_failed", node_all_failed_fix)
    graph.add_node("assemble_decision", node_assemble_decision)
    graph.add_node("verify", node_verify)

    graph.add_edge(START, "filter")
    graph.add_conditional_edges(
        "filter", route_after_filter,
        {"score_r1": "score_r1", "all_failed": "all_failed"},
    )

    graph.add_conditional_edges(
        "score_r1", route_after_r1,
        {
            "finalize_r1": "finalize_r1",
            "score_r2": "score_r2",
            "pick_for_fix": "pick_for_fix",
        },
    )

    graph.add_edge("finalize_r1", "assemble_decision")
    graph.add_edge("score_r2", "finalize_r2")
    graph.add_edge("finalize_r2", "assemble_decision")
    graph.add_edge("pick_for_fix", "smart_fix")
    graph.add_edge("smart_fix", "assemble_decision")
    graph.add_edge("all_failed", "assemble_decision")
    graph.add_edge("assemble_decision", "verify")
    graph.add_edge("verify", END)

    return graph.compile()

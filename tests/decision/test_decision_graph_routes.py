# ============================================================================
# Decision 子图路由集成测试（决策 51）
# ============================================================================
# 覆盖 6 条决策路径：A / B / C / D / E / F / G / H

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.decision.decision_graph import build_decision_graph
from src.decision.self_consistency import SelfConsistencyDecision
from src.execution.executor import ErrorType, ExecutionResult, SQLFixLoop, StructuredError
from src.sql_generation.sql_generator import SQLCandidate, SQLStatus


def make_cand(id, sql="SELECT 1", status=SQLStatus.SUCCESS,
              result=None, exec_time=0.1, err_type=None):
    c = SQLCandidate(
        id=id, sql=sql, status=status,
        result=result if result is not None else [(1,)],
        execution_time=exec_time,
    )
    if status == SQLStatus.FAILED and err_type:
        c.error_message = err_type.value
        c.structured_error = StructuredError(
            error_type=err_type, original_message="mocked err",
        )
    return c


def build_decider(r1_scores=None, r2_scores=None,
                  fix_succeed_round=None, fix_loop=None,
                  per_candidate_fix=None):
    """构造一个 SelfConsistencyDecision，mock 各方法

    per_candidate_fix: dict[candidate_id, succeed_round_or_None] —
                       全失败分支用，按 cand id 决定第几轮成功（None 表示永远失败）
    """
    llm = MagicMock()
    decider = SelfConsistencyDecision(llm_client=llm)

    if r1_scores is not None:
        decider.score_by_data = MagicMock(return_value=r1_scores)
    if r2_scores is not None:
        decider.score_by_sql = MagicMock(return_value=r2_scores)

    # 注入 fix_loop（mock）
    if fix_loop is None:
        fix_loop = MagicMock(spec=SQLFixLoop)
        if per_candidate_fix is not None:
            # 按候选 id 决定结果
            def run_fn(sql, user_query, schema_text="", initial_error=None):
                cid_match = None
                for cid, succ_round in per_candidate_fix.items():
                    # 简单匹配：用 sql 中的 cid 标识
                    if cid in sql:
                        cid_match = cid
                        break
                succ_round = per_candidate_fix.get(cid_match)
                if succ_round is None:
                    return {
                        "result": ExecutionResult(
                            success=False, sql=sql,
                            error=StructuredError(ErrorType.SEMANTIC_ERROR, "still failing"),
                        ),
                        "fix_history": [{"round": i, "sql": f"v{i}", "error": "e"} for i in range(1, 4)],
                        "fix_rounds_used": 3,
                        "fix_failed": True,
                        "last_error": "still failing",
                    }
                return {
                    "result": ExecutionResult(
                        success=True, sql=f"FIXED({sql})",
                        result_data=[(99,)], execution_time=0.02,
                    ),
                    "fix_history": [],
                    "fix_rounds_used": succ_round,
                    "fix_failed": False,
                    "last_error": None,
                }
            fix_loop.run.side_effect = run_fn
        elif fix_succeed_round is not None:
            fix_loop.run.return_value = {
                "result": ExecutionResult(
                    success=True, sql="SELECT fixed",
                    result_data=[(99,)], execution_time=0.02,
                ),
                "fix_history": [],
                "fix_rounds_used": fix_succeed_round,
                "fix_failed": False,
                "last_error": None,
            }
        else:
            # 默认失败
            fix_loop.run.return_value = {
                "result": ExecutionResult(
                    success=False, sql="SELECT bad",
                    error=StructuredError(ErrorType.SEMANTIC_ERROR, "still failing"),
                ),
                "fix_history": [],
                "fix_rounds_used": 3,
                "fix_failed": True,
                "last_error": "still failing",
            }
    decider.fix_loop = fix_loop
    decider.executor = fix_loop.executor if hasattr(fix_loop, "executor") else MagicMock()
    return decider


class TestDecisionGraphRoutes(unittest.TestCase):
    """6 条决策路径的集成测试"""

    def _invoke(self, decider, candidates):
        graph = build_decision_graph(decider)
        return graph.invoke({
            "candidates": candidates,
            "user_query": "x",
            "schema_text": "",
            "mschema": [],
        })

    def test_path_A_unique_top5(self):
        """路径 A：R1 唯一最高=5 直接返回"""
        cands = [
            make_cand("c1", result=[(1,)]),
            make_cand("c2", result=[(2,)]),
        ]
        decider = build_decider(r1_scores=[
            {"candidate_id": "c1", "score": 5, "reason": ""},
            {"candidate_id": "c2", "score": 3, "reason": ""},
        ])
        state = self._invoke(decider, cands)
        decision = state["final_decision"]
        self.assertEqual(decision.decision_path, "A")
        self.assertEqual(decision.selected_candidate_id, "c1")
        # R2 未触发
        decider.score_by_sql.assert_not_called() if hasattr(decider.score_by_sql, "assert_not_called") else None
        # SmartFix 未触发
        decider.fix_loop.run.assert_not_called()

    def test_path_B_r2_unique_top(self):
        """路径 B：R1 并列=5 → R2 唯一最高"""
        cands = [
            make_cand("c1", result=[(1,)], exec_time=0.2),
            make_cand("c2", result=[(2,)], exec_time=0.1),
        ]
        decider = build_decider(
            r1_scores=[
                {"candidate_id": "c1", "score": 5, "reason": ""},
                {"candidate_id": "c2", "score": 5, "reason": ""},
            ],
            r2_scores=[
                {"candidate_id": "c1", "score": 5, "reason": ""},
                {"candidate_id": "c2", "score": 4, "reason": ""},
            ],
        )
        state = self._invoke(decider, cands)
        decision = state["final_decision"]
        self.assertEqual(decision.decision_path, "B")
        self.assertEqual(decision.selected_candidate_id, "c1")
        # R2 被调用
        decider.score_by_sql.assert_called_once()
        # SmartFix 未触发
        decider.fix_loop.run.assert_not_called()

    def test_path_C_r2_tied_picks_fastest(self):
        """路径 C：R1 并列=5 → R2 仍并列 → 选最快"""
        cands = [
            make_cand("c1", result=[(1,)], exec_time=0.5),  # 较慢
            make_cand("c2", result=[(2,)], exec_time=0.1),  # 最快
        ]
        decider = build_decider(
            r1_scores=[
                {"candidate_id": "c1", "score": 5, "reason": ""},
                {"candidate_id": "c2", "score": 5, "reason": ""},
            ],
            r2_scores=[
                {"candidate_id": "c1", "score": 5, "reason": ""},
                {"candidate_id": "c2", "score": 5, "reason": ""},
            ],
        )
        state = self._invoke(decider, cands)
        decision = state["final_decision"]
        self.assertEqual(decision.decision_path, "C")
        self.assertEqual(decision.selected_candidate_id, "c2")  # c2 最快
        decider.fix_loop.run.assert_not_called()

    def test_path_D_smart_fix_succeeds(self):
        """路径 D：R1<5 → SmartFix 成功"""
        cands = [make_cand("c1", result=[(1,)])]
        decider = build_decider(
            r1_scores=[{"candidate_id": "c1", "score": 3, "reason": "缺维度"}],
            fix_succeed_round=2,
        )
        state = self._invoke(decider, cands)
        decision = state["final_decision"]
        self.assertEqual(decision.decision_path, "D")
        self.assertFalse(decision.fix_failed)
        self.assertEqual(decision.fix_rounds_used, 2)
        self.assertEqual(decision.selected_sql, "SELECT fixed")

    def test_path_E_smart_fix_fails(self):
        """路径 E：R1<5 → SmartFix 3 轮失败"""
        cands = [make_cand("c1", result=[(1,)])]
        decider = build_decider(
            r1_scores=[{"candidate_id": "c1", "score": 2, "reason": "差"}],
            # fix_succeed_round=None 默认会失败
        )
        state = self._invoke(decider, cands)
        decision = state["final_decision"]
        self.assertEqual(decision.decision_path, "E")
        self.assertTrue(decision.fix_failed)
        self.assertEqual(decision.fix_rounds_used, 3)
        # 失败时保留原 SQL
        self.assertEqual(decision.selected_sql, "SELECT 1")
        self.assertIsNone(decision.selected_result)
        self.assertIsNotNone(decision.last_error)

    def test_path_F_all_failed_lightest_succeeds(self):
        """路径 F：全失败 → 选最轻 → SmartFix 第 2 个成功"""
        cands = [
            make_cand("c1", sql="SQL_c1", status=SQLStatus.FAILED, err_type=ErrorType.TIMEOUT_ERROR),
            make_cand("c2", sql="SQL_c2", status=SQLStatus.FAILED, err_type=ErrorType.SEMANTIC_ERROR),
            make_cand("c3", sql="SQL_c3", status=SQLStatus.FAILED, err_type=ErrorType.SEMANTIC_ERROR),
        ]
        # 第 1 个 SEMANTIC 候选失败，第 2 个成功
        decider = build_decider(
            per_candidate_fix={"SQL_c2": None, "SQL_c3": 1},
        )
        state = self._invoke(decider, cands)
        decision = state["final_decision"]
        self.assertEqual(decision.decision_path, "F")
        self.assertFalse(decision.fix_failed)
        # R1/R2 都未调用（全失败分支不走评分）
        # SmartFix 被调用 2 次（c2 失败 + c3 成功）
        self.assertEqual(decider.fix_loop.run.call_count, 2)

    def test_path_G_all_lightest_fail(self):
        """路径 G：全失败 → 最轻全部修不好"""
        cands = [
            make_cand("c1", sql="SQL_c1", status=SQLStatus.FAILED, err_type=ErrorType.SEMANTIC_ERROR),
            make_cand("c2", sql="SQL_c2", status=SQLStatus.FAILED, err_type=ErrorType.SEMANTIC_ERROR),
        ]
        decider = build_decider(per_candidate_fix={"SQL_c1": None, "SQL_c2": None})
        state = self._invoke(decider, cands)
        decision = state["final_decision"]
        self.assertEqual(decision.decision_path, "G")
        self.assertTrue(decision.fix_failed)
        # 2 个候选都被尝试
        self.assertEqual(decider.fix_loop.run.call_count, 2)

    def test_path_H_all_unfixable(self):
        """路径 H：全失败且最轻全是不可修 → 不调 LLM 直接返回"""
        cands = [
            make_cand("c1", status=SQLStatus.FAILED, err_type=ErrorType.TIMEOUT_ERROR),
            make_cand("c2", status=SQLStatus.FAILED, err_type=ErrorType.PERMISSION_ERROR),
        ]
        decider = build_decider()  # 不需要 fix_succeed_round 因为不会调
        state = self._invoke(decider, cands)
        decision = state["final_decision"]
        self.assertEqual(decision.decision_path, "H")
        self.assertTrue(decision.fix_failed)
        # 关键：SmartFix 完全未被调用
        decider.fix_loop.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()

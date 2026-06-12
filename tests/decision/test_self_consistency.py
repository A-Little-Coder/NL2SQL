# ============================================================================
# SelfConsistencyDecision 测试用例（决策 51 重写版）
# ============================================================================
# 覆盖保留的方法：
#   - score_by_data / score_by_sql
#   - _pick_from_scores / pick_lightest_failures
#   - _truncate_cell / _format_candidate_data_preview
# ============================================================================


import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.decision.self_consistency import (
    SelfConsistencyDecision, DecisionResult,
)
from src.sql_generation.sql_generator import SQLCandidate, SQLStatus


def make_candidate(id, sql, result=None, exec_time=0.1, status=SQLStatus.SUCCESS):
    """快速构造一个 candidate"""
    return SQLCandidate(
        id=id, sql=sql,
        status=status,
        result=result,
        execution_time=exec_time,
    )


class TestPickFromScores(unittest.TestCase):
    """_pick_from_scores 静态方法测试"""

    def test_single_best(self):
        cands = [make_candidate("c1", "SQL1"), make_candidate("c2", "SQL2")]
        scores = [
            {"candidate_id": "c1", "score": 3, "reason": ""},
            {"candidate_id": "c2", "score": 5, "reason": ""},
        ]
        best_id, is_tied, top = SelfConsistencyDecision._pick_from_scores(cands, scores)
        self.assertEqual(best_id, "c2")
        self.assertFalse(is_tied)
        self.assertEqual(top, 5)

    def test_tied_picks_fastest(self):
        cands = [
            make_candidate("c1", "SQL1", exec_time=0.5),
            make_candidate("c2", "SQL2", exec_time=0.1),
        ]
        scores = [
            {"candidate_id": "c1", "score": 5, "reason": ""},
            {"candidate_id": "c2", "score": 5, "reason": ""},
        ]
        best_id, is_tied, top = SelfConsistencyDecision._pick_from_scores(cands, scores)
        self.assertEqual(best_id, "c2")  # 更快
        self.assertTrue(is_tied)
        self.assertEqual(top, 5)

    def test_empty_scores_fallback(self):
        cands = [make_candidate("c1", "SQL1")]
        best_id, is_tied, top = SelfConsistencyDecision._pick_from_scores(cands, [])
        self.assertEqual(best_id, "c1")
        self.assertEqual(top, 0)

    def test_empty_scores_and_cands(self):
        best_id, is_tied, top = SelfConsistencyDecision._pick_from_scores([], [])
        self.assertIsNone(best_id)


class TestPickLightestFailures(unittest.TestCase):
    """pick_lightest_failures 静态方法测试"""

    def test_empty_list_returns_empty(self):
        result = SelfConsistencyDecision.pick_lightest_failures([])
        self.assertEqual(result, [])

    def test_unfixable_errors_return_empty(self):
        from src.execution.executor import ErrorType, StructuredError
        cands = [
            make_candidate("c1", "SQL1", status=SQLStatus.FAILED),
        ]
        cands[0].structured_error = StructuredError(ErrorType.PERMISSION_ERROR, "denied")
        result = SelfConsistencyDecision.pick_lightest_failures(cands)
        self.assertEqual(result, [])


class TestScoreByData(unittest.TestCase):
    """R1 数据视角评分"""

    def test_no_llm_returns_zero_scores(self):
        decider = SelfConsistencyDecision()
        cands = [make_candidate("c1", "SQL1", result=[(1,)])]
        scores = decider.score_by_data(cands, "test")
        self.assertEqual(len(scores), 1)
        self.assertEqual(scores[0]["score"], 0)

    def test_no_success_candidates_returns_empty(self):
        decider = SelfConsistencyDecision()
        cands = [make_candidate("c1", "SQL1", status=SQLStatus.FAILED)]
        scores = decider.score_by_data(cands, "test")
        self.assertEqual(scores, [])


class TestScoreBySql(unittest.TestCase):
    """R2 SQL 视角评分"""

    def test_no_candidates_returns_empty(self):
        decider = SelfConsistencyDecision()
        scores = decider.score_by_sql([], "test", [])
        self.assertEqual(scores, [])


class TestBuildGraph(unittest.TestCase):
    """子图构建测试"""

    def test_build_graph_returns_compiled(self):
        decider = SelfConsistencyDecision()
        graph = decider.build_graph()
        self.assertIsNotNone(graph)


if __name__ == "__main__":
    unittest.main()

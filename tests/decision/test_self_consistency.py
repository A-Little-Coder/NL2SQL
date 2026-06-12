# ============================================================================
# SelfConsistencyDecision 测试用例
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


class TestComputeResultHash(unittest.TestCase):
    """结果 hash 测试"""

    def setUp(self):
        self.decider = SelfConsistencyDecision()

    def test_same_list_same_hash(self):
        h1 = self.decider.compute_result_hash([(1, "a"), (2, "b")])
        h2 = self.decider.compute_result_hash([(1, "a"), (2, "b")])
        self.assertEqual(h1, h2)

    def test_different_order_same_hash(self):
        """顺序不同的相同集合应该有相同 hash"""
        h1 = self.decider.compute_result_hash([(1, "a"), (2, "b")])
        h2 = self.decider.compute_result_hash([(2, "b"), (1, "a")])
        self.assertEqual(h1, h2)

    def test_different_result_different_hash(self):
        h1 = self.decider.compute_result_hash([(1, "a")])
        h2 = self.decider.compute_result_hash([(2, "b")])
        self.assertNotEqual(h1, h2)

    def test_none_result(self):
        h = self.decider.compute_result_hash(None)
        self.assertEqual(h, "__none__")

    def test_float_precision(self):
        """浮点数应该按 4 位精度规范化"""
        h1 = self.decider.compute_result_hash([(1.123456,)])
        h2 = self.decider.compute_result_hash([(1.123499,)])
        self.assertEqual(h1, h2)


class TestGroupByResult(unittest.TestCase):
    """结果分组测试"""

    def setUp(self):
        self.decider = SelfConsistencyDecision()

    def test_group_same_results(self):
        cands = [
            make_candidate("1", "SQL1", result=[(100,)]),
            make_candidate("2", "SQL2", result=[(100,)]),
            make_candidate("3", "SQL3", result=[(200,)]),
        ]
        groups = self.decider.group_by_result(cands)
        # 应该有 2 个不同的结果组
        success_groups = {k: v for k, v in groups.items() if k != "__failed__"}
        self.assertEqual(len(success_groups), 2)

    def test_failed_candidates_grouped(self):
        cands = [
            make_candidate("1", "SQL1", status=SQLStatus.FAILED),
            make_candidate("2", "SQL2", status=SQLStatus.FAILED),
            make_candidate("3", "SQL3", result=[(100,)]),
        ]
        groups = self.decider.group_by_result(cands)
        self.assertIn("__failed__", groups)
        self.assertEqual(len(groups["__failed__"]), 2)


class TestFindMajority(unittest.TestCase):
    """多数组查找测试"""

    def setUp(self):
        self.decider = SelfConsistencyDecision()

    def test_clear_majority(self):
        groups = {
            "h1": [1, 2, 3],
            "h2": [4],
        }
        has, key, group = self.decider.find_majority_group(groups)
        self.assertTrue(has)
        self.assertEqual(key, "h1")

    def test_no_majority_tie(self):
        groups = {
            "h1": [1, 2],
            "h2": [3, 4],
        }
        # 2/4 = 50%，不超过阈值 0.5
        has, key, group = self.decider.find_majority_group(groups)
        self.assertFalse(has)

    def test_ignore_failed(self):
        """失败组不参与多数计算"""
        groups = {
            "__failed__": [1, 2, 3],
            "h1": [4, 5],
            "h2": [6],
        }
        has, key, group = self.decider.find_majority_group(groups)
        # h1 在成功候选（2+1=3）中占 2/3，是多数
        self.assertTrue(has)
        self.assertEqual(key, "h1")


class TestSelectFastest(unittest.TestCase):
    def test_select_fastest(self):
        decider = SelfConsistencyDecision()
        group = [
            make_candidate("1", "SQL1", exec_time=0.5),
            make_candidate("2", "SQL2", exec_time=0.1),
            make_candidate("3", "SQL3", exec_time=0.3),
        ]
        best = decider.select_fastest_from_group(group)
        self.assertEqual(best.id, "2")


class TestDecide(unittest.TestCase):
    """完整决策流程测试"""

    def test_majority_wins(self):
        decider = SelfConsistencyDecision()
        cands = [
            make_candidate("1", "SQL_A", result=[(100,)], exec_time=0.3),
            make_candidate("2", "SQL_B", result=[(100,)], exec_time=0.1),
            make_candidate("3", "SQL_C", result=[(200,)], exec_time=0.2),
        ]
        result = decider.decide(cands, "test query")
        self.assertEqual(result.selected_sql, "SQL_B")  # 多数组中最快
        self.assertIn("多数一致", result.decision_reason)

    def test_all_different_calls_llm(self):
        mock_llm = MagicMock()
        # 新接口：llm_final_decision 走 stream + parse_json
        mock_llm.stream.return_value = iter([('{"selected": 2, "reason": "更准确"}', None)])
        decider = SelfConsistencyDecision(llm_client=mock_llm)

        cands = [
            make_candidate("1", "SQL_A", result=[(100,)]),
            make_candidate("2", "SQL_B", result=[(200,)]),
            make_candidate("3", "SQL_C", result=[(300,)]),
        ]
        result = decider.decide(cands, "test")
        self.assertEqual(result.selected_sql, "SQL_B")
        self.assertIn("LLM", result.decision_reason)

    def test_all_failed(self):
        decider = SelfConsistencyDecision()
        cands = [
            make_candidate("1", "SQL1", status=SQLStatus.FAILED),
            make_candidate("2", "SQL2", status=SQLStatus.FAILED),
        ]
        result = decider.decide(cands, "test")
        self.assertIsNone(result.selected_sql)
        self.assertIn("失败", result.decision_reason)

    def test_empty_candidates(self):
        decider = SelfConsistencyDecision()
        result = decider.decide([], "test")
        self.assertIsNone(result.selected_sql)
        self.assertEqual(result.decision_reason, "无候选 SQL")

    def test_partial_failure_majority(self):
        """部分失败但仍有多数一致"""
        decider = SelfConsistencyDecision()
        cands = [
            make_candidate("1", "SQL_A", result=[(100,)], exec_time=0.2),
            make_candidate("2", "SQL_B", result=[(100,)], exec_time=0.1),
            make_candidate("3", "SQL_C", status=SQLStatus.FAILED),
        ]
        result = decider.decide(cands, "test")
        self.assertEqual(result.selected_sql, "SQL_B")


if __name__ == "__main__":
    unittest.main()

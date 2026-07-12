# ============================================================================
# TaskDecomposer 单元测试（v2 精简版）
# ============================================================================
# 运行: pytest tests/clarification/test_task_decomposer.py -v
# ============================================================================

import json
import unittest
from unittest.mock import MagicMock

from src.clarification.task_decomposer import TaskDecomposer, PlanResult


def _make_llm_mock(json_dict: dict):
    """构造一个 mock LLM，stream 返回单 chunk JSON。"""
    mock = MagicMock()
    mock.stream.return_value = [(json.dumps(json_dict, ensure_ascii=False), None)]
    return mock


class TestPlanResultDataclass(unittest.TestCase):
    """PlanResult.from_dict 字段校验与降级"""

    def test_execute_single(self):
        r = PlanResult.from_dict({
            "verdict": "execute",
            "intent_type": "single",
            "subqueries": ["查苹果销售额"],
        })
        self.assertEqual(r.verdict, "execute")
        self.assertEqual(r.intent_type, "single")
        self.assertEqual(r.subqueries, ["查苹果销售额"])

    def test_execute_multi(self):
        r = PlanResult.from_dict({
            "verdict": "execute",
            "intent_type": "multi",
            "subqueries": ["查苹果销售额", "查苹果利润"],
        })
        self.assertEqual(r.intent_type, "multi")
        self.assertEqual(len(r.subqueries), 2)

    def test_clarify_degrades_to_execute(self):
        """clarify verdict 降级为 execute（v2 不再支持 CLARIFY）"""
        r = PlanResult.from_dict({
            "verdict": "clarify",
            "clarify_question": "苹果指公司还是水果？",
            "ambiguities": [{"entity": "苹果", "candidates": ["公司", "水果"]}],
        })
        self.assertEqual(r.verdict, "execute")

    def test_reject_degrades_to_execute(self):
        """reject verdict 降级为 execute"""
        r = PlanResult.from_dict({
            "verdict": "reject",
            "reject_reason": "越权写操作",
        })
        self.assertEqual(r.verdict, "execute")

    def test_unknown_verdict_degrades_to_execute(self):
        """未知 verdict 降级为 execute"""
        r = PlanResult.from_dict({"verdict": "weird", "subqueries": ["x"]})
        self.assertEqual(r.verdict, "execute")

    def test_unknown_intent_type_degrades_to_single(self):
        r = PlanResult.from_dict({"verdict": "execute", "intent_type": "xxx", "subqueries": ["x"]})
        self.assertEqual(r.intent_type, "single")

    def test_to_dict_roundtrip(self):
        r = PlanResult(verdict="execute", intent_type="single", reason="测试")
        d = r.to_dict()
        self.assertEqual(d["verdict"], "execute")
        self.assertEqual(d["intent_type"], "single")


class TestTaskDecomposerReject(unittest.TestCase):
    """TaskDecomposer v2 不再有拒答/改写功能（Rewrite 子图接管）"""

    def test_reject_verdict_degrades_to_execute(self):
        """LLM 返回 reject 时 TaskDecomposer 降级为 execute"""
        mock_llm = _make_llm_mock({"verdict": "reject", "reject_reason": "越权"})
        planner = TaskDecomposer(llm_client=mock_llm)
        result = planner.plan("删除数据")
        self.assertEqual(result.verdict, "execute")

    def test_empty_query_returns_execute(self):
        planner = TaskDecomposer(llm_client=MagicMock())
        result = planner.plan("")
        self.assertEqual(result.verdict, "execute")
        self.assertEqual(result.subqueries, [])

    def test_whitespace_query_returns_execute(self):
        planner = TaskDecomposer(llm_client=MagicMock())
        result = planner.plan("   ")
        self.assertEqual(result.verdict, "execute")


class TestTaskDecomposerNoLLM(unittest.TestCase):
    """无 LLM 时降级为单意图执行"""

    def test_no_llm_degrades_to_execute_single(self):
        planner = TaskDecomposer(llm_client=None)
        result = planner.plan("查苹果的销售额")
        self.assertEqual(result.verdict, "execute")
        self.assertEqual(result.intent_type, "single")
        self.assertEqual(result.subqueries, ["查苹果的销售额"])


class TestTaskDecomposerLLM(unittest.TestCase):
    """有 LLM 时的 EXECUTE 裁决"""

    def test_execute_single(self):
        mock_llm = _make_llm_mock({
            "verdict": "execute",
            "intent_type": "single",
            "subqueries": ["查询洛杉矶的公立学校数量"],
            "reason": "意图清晰",
        })
        planner = TaskDecomposer(llm_client=mock_llm)
        result = planner.plan("查询洛杉矶的公立学校数量")
        self.assertEqual(result.verdict, "execute")
        self.assertEqual(result.intent_type, "single")
        self.assertEqual(len(result.subqueries), 1)

    def test_execute_multi_decomposition(self):
        """多意图分解"""
        mock_llm = _make_llm_mock({
            "verdict": "execute",
            "intent_type": "multi",
            "subqueries": ["查苹果的销售额", "查苹果的利润"],
            "reason": "两个独立问数意图",
        })
        planner = TaskDecomposer(llm_client=mock_llm)
        result = planner.plan("查一下苹果的销售额和利润")
        self.assertEqual(result.verdict, "execute")
        self.assertEqual(result.intent_type, "multi")
        self.assertEqual(len(result.subqueries), 2)


class TestTaskDecomposerFallback(unittest.TestCase):
    """降级与兜底"""

    def test_llm_exception_degrades_to_execute(self):
        """LLM 抛异常降级为单意图执行"""
        mock_llm = MagicMock()
        mock_llm.stream.side_effect = RuntimeError("API 超时")
        planner = TaskDecomposer(llm_client=mock_llm)
        result = planner.plan("查苹果销售额")
        self.assertEqual(result.verdict, "execute")
        self.assertEqual(result.subqueries, ["查苹果销售额"])

    def test_execute_empty_subqueries_fallback(self):
        """execute 但 subqueries 为空 → 用原始 query 兜底"""
        mock_llm = _make_llm_mock({"verdict": "execute", "subqueries": []})
        planner = TaskDecomposer(llm_client=mock_llm)
        result = planner.plan("查苹果销售额")
        self.assertEqual(result.verdict, "execute")
        self.assertEqual(result.subqueries, ["查苹果销售额"])

    def test_single_force_one_subquery(self):
        """single 意图强制 subqueries 长度为 1"""
        mock_llm = _make_llm_mock({
            "verdict": "execute",
            "intent_type": "single",
            "subqueries": ["a", "b", "c"],
        })
        planner = TaskDecomposer(llm_client=mock_llm)
        result = planner.plan("查苹果销售额")
        self.assertEqual(result.intent_type, "single")
        self.assertEqual(len(result.subqueries), 1)


if __name__ == "__main__":
    unittest.main()
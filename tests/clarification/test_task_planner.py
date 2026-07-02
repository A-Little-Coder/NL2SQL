# ============================================================================
# TaskPlanner 单元测试（决策 9/10）
# ============================================================================
# 运行: pytest tests/clarification/test_task_planner.py -v
# ============================================================================

import json
import unittest
from unittest.mock import MagicMock

from src.clarification.task_planner import TaskPlanner, PlanResult


def _make_llm_mock(json_dict: dict):
    """构造一个 mock LLM，stream 返回单 chunk JSON。

    llm_client.stream(messages, as_json=...) 应返回迭代器，元素为 (content, reasoning)。
    stream_with_sse 会累积 content，再 parse_json 解析。
    """
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

    def test_clarify(self):
        r = PlanResult.from_dict({
            "verdict": "clarify",
            "clarify_question": "苹果指公司还是水果？",
            "ambiguities": [{"entity": "苹果", "candidates": ["公司", "水果"]}],
        })
        self.assertEqual(r.verdict, "clarify")
        self.assertEqual(r.clarify_question, "苹果指公司还是水果？")
        self.assertEqual(r.ambiguities[0]["entity"], "苹果")

    def test_reject(self):
        r = PlanResult.from_dict({
            "verdict": "reject",
            "reject_reason": "越权写操作",
        })
        self.assertEqual(r.verdict, "reject")
        self.assertEqual(r.reject_reason, "越权写操作")

    def test_unknown_verdict_degrades_to_execute(self):
        """未知 verdict 降级为 execute"""
        r = PlanResult.from_dict({"verdict": "weird", "subqueries": ["x"]})
        self.assertEqual(r.verdict, "execute")

    def test_unknown_intent_type_degrades_to_single(self):
        r = PlanResult.from_dict({"verdict": "execute", "intent_type": "xxx", "subqueries": ["x"]})
        self.assertEqual(r.intent_type, "single")

    def test_to_dict_roundtrip(self):
        r = PlanResult(verdict="reject", reject_reason="越权", reason="写操作")
        d = r.to_dict()
        self.assertEqual(d["verdict"], "reject")
        self.assertEqual(d["reject_reason"], "越权")


class TestWriteOperationDetection(unittest.TestCase):
    """写操作硬性检测（先于 LLM，决策 9）"""

    def test_delete_sql_keyword(self):
        self.assertTrue(TaskPlanner._detect_write_operation("delete from users"))

    def test_drop_keyword(self):
        self.assertTrue(TaskPlanner._detect_write_operation("drop table orders"))

    def test_chinese_delete(self):
        self.assertTrue(TaskPlanner._detect_write_operation("帮我把这条记录删除"))

    def test_chinese_update(self):
        self.assertTrue(TaskPlanner._detect_write_operation("更新一下这个订单的状态"))

    def test_normal_query_not_write(self):
        self.assertFalse(TaskPlanner._detect_write_operation("查询苹果的销售额"))

    def test_column_name_not_write(self):
        """updated_at 列名不应误判为写操作（词边界）"""
        self.assertFalse(TaskPlanner._detect_write_operation("查 updated_at 字段"))

    def test_created_at_not_write(self):
        self.assertFalse(TaskPlanner._detect_write_operation("按 created_at 排序"))


class TestTaskPlannerReject(unittest.TestCase):
    """REJECT 路径"""

    def test_write_operation_rejected_without_llm(self):
        """写操作应直接 REJECT，不调用 LLM"""
        mock_llm = _make_llm_mock({"verdict": "execute", "subqueries": ["x"]})
        planner = TaskPlanner(llm_client=mock_llm)
        result = planner.plan("删除 users 表里所有数据")
        self.assertEqual(result.verdict, "reject")
        self.assertIn("写操作", result.reject_reason)
        mock_llm.stream.assert_not_called()  # 没调 LLM

    def test_empty_query_rejected(self):
        planner = TaskPlanner(llm_client=MagicMock())
        result = planner.plan("")
        self.assertEqual(result.verdict, "reject")
        self.assertIn("空", result.reject_reason)

    def test_whitespace_query_rejected(self):
        planner = TaskPlanner(llm_client=MagicMock())
        result = planner.plan("   ")
        self.assertEqual(result.verdict, "reject")


class TestTaskPlannerNoLLM(unittest.TestCase):
    """无 LLM 时降级为单意图执行"""

    def test_no_llm_degrades_to_execute_single(self):
        planner = TaskPlanner(llm_client=None)
        result = planner.plan("查苹果的销售额")
        self.assertEqual(result.verdict, "execute")
        self.assertEqual(result.intent_type, "single")
        self.assertEqual(result.subqueries, ["查苹果的销售额"])


class TestTaskPlannerLLM(unittest.TestCase):
    """有 LLM 时的三选一裁决"""

    def test_execute_single(self):
        mock_llm = _make_llm_mock({
            "verdict": "execute",
            "intent_type": "single",
            "subqueries": ["查询洛杉矶的公立学校数量"],
            "reason": "意图清晰",
        })
        planner = TaskPlanner(llm_client=mock_llm)
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
        planner = TaskPlanner(llm_client=mock_llm)
        result = planner.plan("查一下苹果的销售额和利润")
        self.assertEqual(result.verdict, "execute")
        self.assertEqual(result.intent_type, "multi")
        self.assertEqual(len(result.subqueries), 2)

    def test_clarify_entity_ambiguity(self):
        """实体多义触发反问"""
        mock_llm = _make_llm_mock({
            "verdict": "clarify",
            "clarify_question": "您说的苹果是指公司还是水果？",
            "ambiguities": [{"entity": "苹果", "candidates": ["Apple Inc.", "水果"]}],
            "reason": "苹果多义",
        })
        planner = TaskPlanner(llm_client=mock_llm)
        result = planner.plan("查一下苹果的销售额")
        self.assertEqual(result.verdict, "clarify")
        self.assertIn("苹果", result.clarify_question)

    def test_clarify_missing_dimension(self):
        """缺失维度触发反问"""
        mock_llm = _make_llm_mock({
            "verdict": "clarify",
            "clarify_question": "您想查询哪类商品的销量？",
            "reason": "缺失商品类别维度",
        })
        planner = TaskPlanner(llm_client=mock_llm)
        result = planner.plan("最近的数据")
        self.assertEqual(result.verdict, "clarify")

    def test_reject_out_of_scope(self):
        """超范围拒答"""
        mock_llm = _make_llm_mock({
            "verdict": "reject",
            "reject_reason": "查询内容超出当前数据库范围",
            "reason": "无相关业务域",
        })
        planner = TaskPlanner(llm_client=mock_llm)
        result = planner.plan("查一下火星的温度")
        self.assertEqual(result.verdict, "reject")
        self.assertIn("范围", result.reject_reason)


class TestTaskPlannerFallback(unittest.TestCase):
    """降级与兜底"""

    def test_llm_exception_degrades_to_execute(self):
        """LLM 抛异常降级为单意图执行"""
        mock_llm = MagicMock()
        mock_llm.stream.side_effect = RuntimeError("API 超时")
        planner = TaskPlanner(llm_client=mock_llm)
        result = planner.plan("查苹果销售额")
        self.assertEqual(result.verdict, "execute")
        self.assertEqual(result.subqueries, ["查苹果销售额"])

    def test_execute_empty_subqueries_fallback(self):
        """execute 但 subqueries 为空 → 用原始 query 兜底"""
        mock_llm = _make_llm_mock({"verdict": "execute", "subqueries": []})
        planner = TaskPlanner(llm_client=mock_llm)
        result = planner.plan("查苹果销售额")
        self.assertEqual(result.verdict, "execute")
        self.assertEqual(result.subqueries, ["查苹果销售额"])

    def test_single_force_one_subquery(self):
        """single 意图强制 subqueries 长度为 1"""
        mock_llm = _make_llm_mock({
            "verdict": "execute",
            "intent_type": "single",
            "subqueries": ["a", "b", "c"],  # LLM 错误返回多个
        })
        planner = TaskPlanner(llm_client=mock_llm)
        result = planner.plan("查苹果销售额")
        self.assertEqual(result.intent_type, "single")
        self.assertEqual(len(result.subqueries), 1)


class TestClarifiedContext(unittest.TestCase):
    """反问 resume 时携带澄清上下文"""

    def test_plan_with_clarified_answer(self):
        """resume 时 clarified 作为上下文传给 LLM"""
        mock_llm = _make_llm_mock({
            "verdict": "execute",
            "intent_type": "single",
            "subqueries": ["查 Apple 公司的销售额"],
        })
        planner = TaskPlanner(llm_client=mock_llm)
        result = planner.plan("查苹果的销售额", clarified="指 Apple 公司")
        self.assertEqual(result.verdict, "execute")
        # 验证 LLM 被调用
        mock_llm.stream.assert_called_once()


if __name__ == "__main__":
    unittest.main()

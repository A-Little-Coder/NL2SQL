# ============================================================================
# AnswerabilityChecker 单元测试
# ============================================================================
# 运行: pytest tests/verification/test_answerability.py -v
# ============================================================================


import unittest
from unittest.mock import MagicMock

from src.verification.answerability import AnswerabilityChecker, AnswerabilityResult


class TestAnswerabilityResult(unittest.TestCase):
    """AnswerabilityResult 数据类测试"""

    def test_should_reject_false(self):
        r = AnswerabilityResult(answerable="false")
        self.assertTrue(r.should_reject)

    def test_should_not_reject_true(self):
        r = AnswerabilityResult(answerable="true")
        self.assertFalse(r.should_reject)

    def test_should_not_reject_uncertain(self):
        r = AnswerabilityResult(answerable="uncertain")
        self.assertFalse(r.should_reject)

    def test_to_dict(self):
        r = AnswerabilityResult(
            answerable="false",
            confidence=0.9,
            reason="granularity mismatch",
            missing_info="student data",
            granularity_match="asked student but only school level",
        )
        d = r.to_dict()
        self.assertEqual(d["answerable"], "false")
        self.assertEqual(d["confidence"], 0.9)
        self.assertIn("student", d["granularity_match"])


class TestAnswerabilityCheckerNoLLM(unittest.TestCase):
    """无 LLM 时默认放行"""

    def test_no_llm_returns_uncertain(self):
        checker = AnswerabilityChecker(llm_client=None)
        result = checker.check("测试查询", mschema=[])
        self.assertEqual(result.answerable, "uncertain")

    def test_empty_schema_returns_false(self):
        """Schema 为空时即使有 LLM 也直接判 false"""
        mock_llm = MagicMock()
        checker = AnswerabilityChecker(llm_client=mock_llm)
        result = checker.check("任何问题", mschema=[])
        self.assertEqual(result.answerable, "false")
        # 不应该调用 LLM
        mock_llm.chat_json.assert_not_called()


class TestAnswerabilityCheckerWithLLM(unittest.TestCase):
    """使用 Mock LLM 测试核心判断逻辑"""

    def _make_mschema(self):
        """构造模拟的 MSchema 表列表"""
        tbl = MagicMock()
        tbl.name = "satscores"
        tbl.description = "学校 SAT 分数"
        tbl.row_count = 2269
        col1 = MagicMock()
        col1.name = "AvgScrRead"
        col1.data_type = "INTEGER"
        col1.description = "平均阅读分数"
        col1.sample_values = [417, 505, 395]
        col1.is_primary_key = False
        col1.is_foreign_key = True
        col1.references = "schools.cds"
        col2 = MagicMock()
        col2.name = "AvgScrMath"
        col2.data_type = "INTEGER"
        col2.description = "平均数学分数"
        col2.sample_values = [418, 546, 387]
        col2.is_primary_key = False
        col2.is_foreign_key = False
        col2.references = None
        tbl.columns = [col1, col2]
        return [tbl]

    def test_granularity_mismatch_returns_false(self):
        """问学生但只有学校级别数据 → false"""
        mock_llm = MagicMock()
        mock_llm.chat_json.return_value = {
            "answerable": "false",
            "confidence": 0.85,
            "reason": "数据库只有学校级别的 SAT 分数，没有学生个体数据",
            "missing_info": "学生粒度的成绩数据",
            "granularity_match": "问'每个学生'但数据粒度为学校",
        }
        checker = AnswerabilityChecker(llm_client=mock_llm)
        result = checker.check(
            "每个学生的各科平均分是怎么样的",
            mschema=self._make_mschema(),
        )
        self.assertEqual(result.answerable, "false")
        self.assertTrue(result.should_reject)
        self.assertIn("学生", result.reason)

    def test_clearly_answerable_returns_true(self):
        """明确可回答 → true"""
        mock_llm = MagicMock()
        mock_llm.chat_json.return_value = {
            "answerable": "true",
            "confidence": 0.95,
            "reason": "satscores 表包含阅读和数学分数，可回答各科平均分",
            "missing_info": "",
            "granularity_match": "问题未指定学生粒度，学校级别数据可满足",
        }
        checker = AnswerabilityChecker(llm_client=mock_llm)
        result = checker.check(
            "各校的 SAT 平均分是多少",
            mschema=self._make_mschema(),
        )
        self.assertEqual(result.answerable, "true")
        self.assertFalse(result.should_reject)

    def test_uncertain_passes_through(self):
        """不确定 → 放行"""
        mock_llm = MagicMock()
        mock_llm.chat_json.return_value = {
            "answerable": "uncertain",
            "confidence": 0.5,
            "reason": "可能有相关信息但需要进一步验证",
            "missing_info": "不确定是否有学生级别数据",
            "granularity_match": "不确定",
        }
        checker = AnswerabilityChecker(llm_client=mock_llm)
        result = checker.check(
            "有没有关于成绩的数据",
            mschema=self._make_mschema(),
        )
        self.assertEqual(result.answerable, "uncertain")
        self.assertFalse(result.should_reject)

    def test_invalid_answerable_defaults_to_uncertain(self):
        """LLM 返回无效值 → 退回 uncertain"""
        mock_llm = MagicMock()
        mock_llm.chat_json.return_value = {
            "answerable": "maybe",
            "confidence": 0.3,
            "reason": "不确定",
        }
        checker = AnswerabilityChecker(llm_client=mock_llm)
        result = checker.check("查询", mschema=self._make_mschema())
        self.assertEqual(result.answerable, "uncertain")

    def test_llm_failure_returns_uncertain(self):
        """LLM 调用失败 → 默认放行"""
        mock_llm = MagicMock()
        mock_llm.chat_json.side_effect = Exception("API 超时")
        checker = AnswerabilityChecker(llm_client=mock_llm)
        result = checker.check("查询", mschema=self._make_mschema())
        self.assertEqual(result.answerable, "uncertain")


class TestShouldProceed(unittest.TestCase):
    """should_proceed 方法测试"""

    def test_loose_mode_false_rejects(self):
        checker = AnswerabilityChecker(strictness="loose")
        r = AnswerabilityResult(answerable="false")
        self.assertFalse(checker.should_proceed(r))

    def test_loose_mode_uncertain_passes(self):
        checker = AnswerabilityChecker(strictness="loose")
        r = AnswerabilityResult(answerable="uncertain", confidence=0.3)
        self.assertTrue(checker.should_proceed(r))

    def test_strict_mode_low_confidence_uncertain_rejects(self):
        checker = AnswerabilityChecker(strictness="strict")
        r = AnswerabilityResult(answerable="uncertain", confidence=0.3)
        self.assertFalse(checker.should_proceed(r))

    def test_strict_mode_high_confidence_uncertain_passes(self):
        checker = AnswerabilityChecker(strictness="strict")
        r = AnswerabilityResult(answerable="uncertain", confidence=0.7)
        self.assertTrue(checker.should_proceed(r))

    def test_strict_mode_true_passes(self):
        checker = AnswerabilityChecker(strictness="strict")
        r = AnswerabilityResult(answerable="true", confidence=0.9)
        self.assertTrue(checker.should_proceed(r))


if __name__ == "__main__":
    unittest.main(verbosity=2)

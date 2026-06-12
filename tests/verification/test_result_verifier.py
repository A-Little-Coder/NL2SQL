# ============================================================================
# ResultVerifier 单元测试
# ============================================================================
# 运行: pytest tests/verification/test_result_verifier.py -v
# ============================================================================


import unittest
from unittest.mock import MagicMock

from src.verification.result_verifier import ResultVerifier, VerificationResult


class TestVerificationResult(unittest.TestCase):
    """VerificationResult 数据类测试"""

    def test_should_reject_false(self):
        r = VerificationResult(trustworthy="false")
        self.assertTrue(r.should_reject)

    def test_should_not_reject_true(self):
        r = VerificationResult(trustworthy="true")
        self.assertFalse(r.should_reject)

    def test_to_dict(self):
        r = VerificationResult(
            trustworthy="false",
            reason="granularity mismatch",
            granularity_match="asked student but got school",
            semantic_alignment="misaligned",
        )
        d = r.to_dict()
        self.assertEqual(d["trustworthy"], "false")
        self.assertIn("granularity", d["reason"])


class TestResultVerifierNoLLM(unittest.TestCase):
    """无 LLM 时默认可信"""

    def test_no_llm_returns_trustworthy(self):
        verifier = ResultVerifier(llm_client=None)
        result = verifier.verify("query", "SELECT 1", [{"col": "val"}])
        self.assertEqual(result.trustworthy, "true")

    def test_no_sql_returns_false(self):
        verifier = ResultVerifier(llm_client=MagicMock())
        result = verifier.verify("query", "", None)
        self.assertEqual(result.trustworthy, "false")


class TestResultVerifierWithLLM(unittest.TestCase):
    """使用 Mock LLM 测试核心验证逻辑"""

    def _sample_result(self):
        """构造模拟执行结果"""
        return [
            {"AvgScrWrite": 417, "AvgScrRead": 418, "AvgScrMath": 418},
            {"AvgScrWrite": 505, "AvgScrRead": 503, "AvgScrMath": 546},
        ]

    def _make_mschema(self):
        tbl = MagicMock()
        tbl.name = "satscores"
        tbl.description = "school SAT scores"
        tbl.row_count = 2269
        col1 = MagicMock()
        col1.name = "AvgScrRead"
        col1.data_type = "INTEGER"
        col1.description = "average reading score"
        col1.sample_values = [417, 505]
        col1.is_primary_key = False
        col1.is_foreign_key = False
        col1.references = None
        col2 = MagicMock()
        col2.name = "AvgScrMath"
        col2.data_type = "INTEGER"
        col2.description = "average math score"
        col2.sample_values = [418, 546]
        col2.is_primary_key = False
        col2.is_foreign_key = False
        col2.references = None
        tbl.columns = [col1, col2]
        return [tbl]

    def test_granularity_mismatch_returns_false(self):
        """SQL 查学校但问学生 → 不可信"""
        mock_llm = MagicMock()
        mock_llm.stream.return_value = iter([(__import__("json").dumps({
            "trustworthy": "false",
            "reason": "SQL queries school-level data but user asked for student-level",
            "granularity_match": "mismatch: asked per-student but SQL returns per-school",
            "semantic_alignment": "school scores used as proxy for student scores",
        }, ensure_ascii=False), None)])
        verifier = ResultVerifier(llm_client=mock_llm)
        result = verifier.verify(
            user_query="each student's average score",
            selected_sql="SELECT AvgScrWrite, AvgScrRead, AvgScrMath FROM satscores",
            result_sample=self._sample_result(),
            mschema=self._make_mschema(),
        )
        self.assertEqual(result.trustworthy, "false")
        self.assertTrue(result.should_reject)

    def test_normal_alignment_returns_true(self):
        """SQL 与问题对齐 → 可信"""
        mock_llm = MagicMock()
        mock_llm.stream.return_value = iter([(__import__("json").dumps({
            "trustworthy": "true",
            "reason": "SQL correctly queries school-level SAT scores matching user request",
            "granularity_match": "aligned: both at school level",
            "semantic_alignment": "result columns match requested dimensions",
        }, ensure_ascii=False), None)])
        verifier = ResultVerifier(llm_client=mock_llm)
        result = verifier.verify(
            user_query="average SAT scores for each school",
            selected_sql="SELECT AvgScrWrite, AvgScrRead, AvgScrMath FROM satscores",
            result_sample=self._sample_result(),
            mschema=self._make_mschema(),
        )
        self.assertEqual(result.trustworthy, "true")
        self.assertFalse(result.should_reject)

    def test_hard_coded_substitution_returns_false(self):
        """用 School 替代学生姓名 → 不可信"""
        mock_llm = MagicMock()
        mock_llm.stream.return_value = iter([(__import__("json").dumps({
            "trustworthy": "false",
            "reason": "User asked for student names but SQL returns school names as substitute",
            "granularity_match": "mismatch",
            "semantic_alignment": "school name used in place of student name (hard substitution)",
        }, ensure_ascii=False), None)])
        verifier = ResultVerifier(llm_client=mock_llm)
        result = verifier.verify(
            user_query="list student names and their scores",
            selected_sql="SELECT School, AvgScrMath FROM satscores",
            result_sample=[{"School": "Lincoln High", "AvgScrMath": 546}],
            mschema=self._make_mschema(),
        )
        self.assertEqual(result.trustworthy, "false")

    def test_llm_failure_returns_trustworthy(self):
        """LLM 调用失败 → 默认放行"""
        mock_llm = MagicMock()
        mock_llm.stream.side_effect = Exception("API timeout")
        verifier = ResultVerifier(llm_client=mock_llm)
        result = verifier.verify(
            user_query="query",
            selected_sql="SELECT 1",
            result_sample=[{"col": 1}],
        )
        self.assertEqual(result.trustworthy, "true")

    def test_invalid_trustworthy_defaults_to_true(self):
        """LLM 返回无效值 → 默认放行"""
        mock_llm = MagicMock()
        mock_llm.stream.return_value = iter([(__import__("json").dumps({
            "trustworthy": "maybe",
            "reason": "uncertain",
        }, ensure_ascii=False), None)])
        verifier = ResultVerifier(llm_client=mock_llm)
        result = verifier.verify(
            user_query="query",
            selected_sql="SELECT 1",
            result_sample=[{"col": 1}],
        )
        self.assertEqual(result.trustworthy, "true")


class TestFormatResultSample(unittest.TestCase):
    """结果样本格式化测试"""

    def test_none_result(self):
        text = ResultVerifier._format_result_sample(None)
        self.assertTrue(len(text) > 0)

    def test_empty_list(self):
        text = ResultVerifier._format_result_sample([])
        self.assertTrue(len(text) > 0)

    def test_dict_rows(self):
        data = [{"name": "Alice", "score": 90}, {"name": "Bob", "score": 85}]
        text = ResultVerifier._format_result_sample(data)
        self.assertIn("name", text)
        self.assertIn("Alice", text)

    def test_tuple_rows(self):
        data = [("Alice", 90), ("Bob", 85)]
        text = ResultVerifier._format_result_sample(data)
        self.assertIn("Alice", text)

    def test_truncates_long_results(self):
        data = [{"id": i} for i in range(20)]
        text = ResultVerifier._format_result_sample(data, max_rows=5)
        self.assertIn("20", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)

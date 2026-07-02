# ============================================================================
# ResultSummarizer 单元测试（决策 15）
# ============================================================================
# 验证：
#   - 多结果 LLM 汇总（按顺序+标注来源）
#   - 数据表结构摘要：只取列名+行数+前5行，不喂整表
#   - LLM 不可用降级拼接
#   - 失败子查询的处理
#
# 运行: pytest tests/clarification/test_result_summarizer.py -v
# ============================================================================

import unittest
from unittest.mock import MagicMock

from src.clarification.result_summarizer import ResultSummarizer, SAMPLE_ROWS


def _make_llm_mock(text: str):
    """mock LLM，stream 返回单 chunk 纯文本。"""
    mock = MagicMock()
    mock.stream.return_value = [(text, None)]
    return mock


def _dict_result(subquery, sql, rows, success=True, error=""):
    """构造一个 dict 形式的子查询结果（数据表）。"""
    return {
        "subquery": subquery,
        "final_sql": sql,
        "final_result": rows,
        "success": success,
        "error": error,
        "decision_path": "A" if success else "FAILED",
    }


class TestStructuralSummary(unittest.TestCase):
    """数据表结构摘要（降 token，决策 15 核心）"""

    def test_dict_rows_summary(self):
        """字典行：提取列名 + 行数 + 前5行"""
        rows = [{"name": f"用户{i}", "age": 20 + i} for i in range(10)]
        r = _dict_result("查用户", "SELECT name, age FROM users", rows)
        summary = ResultSummarizer._format_subquery_summary(1, r)

        # 应含列名
        self.assertIn("name", summary)
        self.assertIn("age", summary)
        # 应含行数 10
        self.assertIn("结果行数: 10", summary)
        # 应只含前 5 行样本（SAMPLE_ROWS=5），不含第 6 行
        self.assertIn("用户0", summary)
        self.assertIn("用户4", summary)
        self.assertNotIn("用户5", summary)
        self.assertNotIn("用户9", summary)

    def test_tuple_rows_summary(self):
        """元组行：自动生成列名"""
        rows = [(1, "a"), (2, "b"), (3, "c")]
        r = _dict_result("查数据", "SELECT * FROM t", rows)
        summary = ResultSummarizer._format_subquery_summary(1, r)
        self.assertIn("结果行数: 3", summary)
        self.assertIn("col_0", summary)

    def test_large_table_only_5_rows(self):
        """大表只取前5行（token 约束验证）"""
        rows = [{"id": i} for i in range(1000)]
        r = _dict_result("查大表", "SELECT id FROM big", rows)
        summary = ResultSummarizer._format_subquery_summary(1, r)
        self.assertIn("结果行数: 1000", summary)
        # 前5行在
        for i in range(5):
            self.assertIn(f"id={i}", summary)
        # 第6行及以后不在
        self.assertNotIn("id=5", summary)
        self.assertNotIn("id=999", summary)

    def test_failed_subquery_summary(self):
        """失败子查询：标注失败原因"""
        r = _dict_result("查失败", "", None, success=False, error="IR 召回为空")
        summary = ResultSummarizer._format_subquery_summary(2, r)
        self.assertIn("失败", summary)
        self.assertIn("IR 召回为空", summary)
        self.assertIn("子查询 2", summary)

    def test_scalar_result(self):
        """标量结果"""
        r = _dict_result("查总数", "SELECT COUNT(*) FROM t", 42)
        summary = ResultSummarizer._format_subquery_summary(1, r)
        self.assertIn("结果行数: 1", summary)
        self.assertIn("42", summary)

    def test_empty_result(self):
        """空结果集"""
        r = _dict_result("查空", "SELECT * FROM empty", [])
        summary = ResultSummarizer._format_subquery_summary(1, r)
        self.assertIn("结果行数: 0", summary)


class TestSummarizeWithLLM(unittest.TestCase):
    """有 LLM 时的汇总"""

    def test_multi_results_llm_summary(self):
        """多结果调 LLM 汇总"""
        llm = _make_llm_mock("这是汇总后的回答，包含苹果销售额和利润。")
        summarizer = ResultSummarizer(llm_client=llm)
        results = [
            _dict_result("查苹果销售额", "SELECT sales", [{"sales": 100}]),
            _dict_result("查苹果利润", "SELECT profit", [{"profit": 30}]),
        ]
        summary = summarizer.summarize(results, user_query="查苹果的销售额和利润")
        self.assertEqual(summary, "这是汇总后的回答，包含苹果销售额和利润。")
        llm.stream.assert_called_once()

    def test_llm_receives_structural_summary_not_full_table(self):
        """验证喂给 LLM 的是结构摘要而非整表"""
        captured_messages = []

        def _capture(messages, **kwargs):
            captured_messages.append(messages)
            return iter([("汇总", None)])

        llm = MagicMock()
        llm.stream = MagicMock(side_effect=_capture)
        summarizer = ResultSummarizer(llm_client=llm)

        big_rows = [{"id": i, "name": f"n{i}"} for i in range(100)]
        results = [_dict_result("查大表", "SELECT * FROM big", big_rows)]
        summarizer.summarize(results, user_query="查大表")

        # 验证 LLM 收到的 prompt 含结构摘要
        prompt_text = str(captured_messages[0])
        self.assertIn("结果行数: 100", prompt_text)
        self.assertIn("列名", prompt_text)
        # 前5行在 prompt 里
        for i in range(5):
            self.assertIn(f"n{i}", prompt_text)
        # 第 50 行不应在 prompt 里（没喂整表）
        self.assertNotIn("n50", prompt_text)

    def test_llm_empty_response_fallback(self):
        """LLM 返回空 → 降级拼接"""
        llm = _make_llm_mock("")
        summarizer = ResultSummarizer(llm_client=llm)
        results = [_dict_result("查", "SELECT 1", [{"x": 1}])]
        summary = summarizer.summarize(results, user_query="查")
        self.assertIn("子查询1", summary)  # 降级拼接

    def test_llm_exception_fallback(self):
        """LLM 抛异常 → 降级拼接"""
        llm = MagicMock()
        llm.stream.side_effect = RuntimeError("API 超时")
        summarizer = ResultSummarizer(llm_client=llm)
        results = [_dict_result("查", "SELECT 1", [{"x": 1}])]
        summary = summarizer.summarize(results, user_query="查")
        self.assertIn("子查询1", summary)


class TestSummarizeNoLLM(unittest.TestCase):
    """无 LLM 时降级拼接"""

    def test_no_llm_fallback(self):
        summarizer = ResultSummarizer(llm_client=None)
        results = [
            _dict_result("查销售额", "SELECT sales", [{"sales": 100}]),
            _dict_result("查利润", "SELECT profit", [{"profit": 30}]),
        ]
        summary = summarizer.summarize(results, user_query="查苹果的销售额和利润")
        self.assertIn("子查询1", summary)
        self.assertIn("子查询2", summary)
        self.assertIn("SELECT sales", summary)

    def test_empty_results(self):
        summarizer = ResultSummarizer(llm_client=None)
        self.assertEqual(summarizer.summarize([], "q"), "")


class TestSampleRowsConstant(unittest.TestCase):
    def test_sample_rows_is_5(self):
        """决策 15：前 5 行"""
        self.assertEqual(SAMPLE_ROWS, 5)


if __name__ == "__main__":
    unittest.main()

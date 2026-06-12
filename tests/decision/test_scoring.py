# ============================================================================
# R1 / R2 评分测试（适配 invoke/stream 新接口）
# ============================================================================
# 验证：
# - R1 评分 prompt 不含 SQL
# - cell 截断 20 字符
# - top-20 行约束
# - 提示包含"节选"说明
# - 失败候选剔除
# - 全失败返回空 list
# - R2 prompt 包含 SQL + R1 评价
# - 返回格式校验
# - _pick_from_scores 逻辑
# ============================================================================

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from utils.llm_client import accumulate, parse_json
from src.decision.prompts import SCORE_BY_DATA_PROMPT, SCORE_BY_SQL_PROMPT
from src.decision.self_consistency import (
    SCORE_DATA_CELL_MAX,
    SCORE_DATA_TOPK,
    SelfConsistencyDecision,
)
from src.sql_generation.sql_generator import SQLCandidate, SQLStatus
from src.execution.executor import ErrorType


def make_stream_result(data_str: str):
    """构造一个 mock stream 返回：一个 (json_text, None) 的迭代器"""
    return iter([(data_str, None)])


def make_cand(cid, sql="SELECT 1", result=None, status=SQLStatus.SUCCESS,
              exec_time=0.1, err_type=ErrorType.SYNTAX_ERROR):
    """构造 SQLCandidate"""
    c = SQLCandidate(id=cid, sql=sql, status=status)
    c.result = result or [(1,)]
    c.execution_time = exec_time
    if status == SQLStatus.FAILED:
        from src.execution.executor import StructuredError
        err_msg = ErrorType.SYNTAX_ERROR.value
        c.error_message = err_msg
        c.structured_error = StructuredError(
            error_type=err_type, original_message=err_msg or err_type.value
        )
    return c


class TestScoreByData(unittest.TestCase):
    """R1 数据视角评分测试"""

    def test_prompt_does_not_contain_sql(self):
        """R1 prompt 必须不含 SQL 代码（强制 LLM 只看数据）"""
        fmt = SCORE_BY_DATA_PROMPT.format_messages(
            topk=20,
            user_query="查询数据",
            candidates_text="候选 ID: c1 （格式化后的数据预览，不带 SQL）",
        )
        user_msg = fmt[1].content
        self.assertNotIn("SELECT", user_msg)

    def test_cell_truncation_20_chars(self):
        """长字符串 cell 应被截断到 20 字符以内"""
        llm = MagicMock()
        decider = SelfConsistencyDecision(llm_client=llm)

        long_str = "X" * 100
        cand = make_cand("c1", result=[(long_str,)])
        result = decider._format_candidate_data_preview(cand, 20)

        # 长字符串不应完整出现
        self.assertNotIn("X" * 100, result)
        # 应被截断到 20 字符
        self.assertIn("X" * 20, result)

    def test_topk_rows_constraint(self):
        """超过 20 行的数据应只展示前 20 行"""
        rows = [(i,) for i in range(50)]
        cand = make_cand("c1", result=rows)
        preview = SelfConsistencyDecision._format_candidate_data_preview(cand, 20)

        # 行数信息应说明总共 50 行
        self.assertIn("返回行数: 50", preview)
        # 有 50 行的话展示有限行数
        self.assertIn(f"前 20 行", preview)

    def test_prompt_includes_top20_disclaimer(self):
        """prompt 必须明示'结果为节选 20 行'提示"""
        fmt = SCORE_BY_DATA_PROMPT.format_messages(
            topk=20,
            user_query="x",
            candidates_text="(格式化后的数据预览)",
        )
        user_msg = fmt[1].content
        self.assertIn("节选的前 20 行", user_msg)
        self.assertIn("不要", user_msg)
        self.assertIn("误判", user_msg)

    def test_failed_candidates_excluded(self):
        """失败候选应从评分池剔除"""
        llm = MagicMock()
        decider = SelfConsistencyDecision(llm_client=llm)

        c1 = make_cand("c1", status=SQLStatus.FAILED,
                       err_type=ErrorType.SYNTAX_ERROR)
        c2 = make_cand("c2", result=[(1,)])
        decider.score_by_data([c1, c2], user_query="x")

        # score_by_data 不会把失败的 c1 送去 LLM，所以 mock stream 不应被调
        # 不过多数情况下它只会对成功的候选发出调用
        self.assertEqual(llm.stream.call_count, 1)

        # 检查传给 LLM 的 messages 文本
        first_call_msgs = llm.stream.call_args.args[0]
        msgs_text = str(first_call_msgs)
        # 失败的 c1 不应出现在 candidates_text 中（即不应有 "候选 ID: c1" 这种实际数据预览）
        # 注意：prompt 的 JSON 示例里写了 "c1"，所以不能 grep "c1"，要匹配实际预览格式
        self.assertNotIn("候选 ID: c1", msgs_text)
        self.assertIn("候选 ID: c2", msgs_text)

    def test_all_failed_returns_empty(self):
        """全失败时 score_by_data 应直接返回空 list（不调 LLM）"""
        llm = MagicMock()
        decider = SelfConsistencyDecision(llm_client=llm)

        cands = [
            make_cand("c1", status=SQLStatus.FAILED, err_type=ErrorType.SYNTAX_ERROR),
            make_cand("c2", status=SQLStatus.FAILED, err_type=ErrorType.SEMANTIC_ERROR),
        ]
        result = decider.score_by_data(cands, user_query="x")

        self.assertEqual(result, [])
        # 全失败不调 LLM
        self.assertEqual(llm.stream.call_count, 0)

    def test_returned_format(self):
        """返回格式应为 [{candidate_id, score, reason}]"""
        llm = MagicMock()
        llm.stream.return_value = make_stream_result(
            '{"scores": [{"candidate_id": "c1", "score": 5, "reason": "完美"}, {"candidate_id": "c2", "score": 3, "reason": "缺少维度"}]}'
        )
        decider = SelfConsistencyDecision(llm_client=llm)

        cands = [make_cand("c1", result=[(1,)]), make_cand("c2", result=[(2,)])]
        result = decider.score_by_data(cands, user_query="x")

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["candidate_id"], "c1")
        self.assertEqual(result[0]["score"], 5)
        self.assertEqual(result[1]["candidate_id"], "c2")
        self.assertEqual(result[1]["score"], 3)


class TestScoreBySQL(unittest.TestCase):
    """R2 SQL 视角评分测试"""

    def test_prompt_includes_sql(self):
        """R2 prompt 必须含 SQL 代码"""
        fmt = SCORE_BY_SQL_PROMPT.format_messages(
            user_query="x",
            candidates_text="候选 ID: c1\nSQL:\nSELECT a, b FROM users",
        )
        user_msg = fmt[1].content
        self.assertIn("SELECT a, b FROM users", user_msg)

    def test_prompt_includes_r1_evaluation(self):
        """R2 prompt 必须带 R1 评分及评价"""
        fmt = SCORE_BY_SQL_PROMPT.format_messages(
            user_query="x",
            candidates_text="候选 ID: c1\n第一轮(数据视角)评分: 5/5\n第一轮评价: 数据完美匹配",
        )
        user_msg = fmt[1].content
        self.assertIn("数据完美匹配", user_msg)
        self.assertIn("5/5", user_msg)

    def test_strict_mode_keywords(self):
        """R2 必须使用严格模式标准"""
        fmt = SCORE_BY_SQL_PROMPT.format_messages(
            user_query="x",
            candidates_text="候选 ID: c1\nSQL:\nSELECT 1",
        )
        user_msg = fmt[1].content
        self.assertIn("严格模式", user_msg)
        self.assertIn("完美满足", user_msg)


class TestPickFromScores(unittest.TestCase):
    """_pick_from_scores 选最高分逻辑测试"""

    def test_unique_top(self):
        cands = [make_cand("c1", exec_time=0.1), make_cand("c2", exec_time=0.2)]
        scores = [
            {"candidate_id": "c1", "score": 5, "reason": ""},
            {"candidate_id": "c2", "score": 3, "reason": ""},
        ]
        best, tied, top = SelfConsistencyDecision._pick_from_scores(cands, scores)
        self.assertEqual(best, "c1")
        self.assertFalse(tied)
        self.assertEqual(top, 5)

    def test_tied_picks_fastest(self):
        """并列时按 execution_time 选最快"""
        cands = [
            make_cand("c1", exec_time=0.5),
            make_cand("c2", exec_time=0.1),
            make_cand("c3", exec_time=0.3),
        ]
        scores = [
            {"candidate_id": "c1", "score": 5, "reason": ""},
            {"candidate_id": "c2", "score": 5, "reason": ""},
            {"candidate_id": "c3", "score": 5, "reason": ""},
        ]
        best, tied, top = SelfConsistencyDecision._pick_from_scores(cands, scores)
        self.assertEqual(best, "c2")
        self.assertTrue(tied)
        self.assertEqual(top, 5)

    def test_tied_equal_time_picks_first(self):
        """并列且执行时间相同时选候选列表中位置靠前的"""
        cands = [
            make_cand("c1", exec_time=0.1),
            make_cand("c2", exec_time=0.1),
        ]
        scores = [
            {"candidate_id": "c1", "score": 5, "reason": ""},
            {"candidate_id": "c2", "score": 5, "reason": ""},
        ]
        best, tied, top = SelfConsistencyDecision._pick_from_scores(cands, scores)
        self.assertEqual(best, "c1")
        self.assertTrue(tied)
        self.assertEqual(top, 5)

    def test_all_zero_scores_returns_fastest(self):
        """全部 0 分仍按"最高分组并列 → 选最快"规则返回（边界）"""
        cands = [
            make_cand("c1", exec_time=0.5),
            make_cand("c2", exec_time=0.1),
        ]
        scores = [
            {"candidate_id": "c1", "score": 0, "reason": ""},
            {"candidate_id": "c2", "score": 0, "reason": ""},
        ]
        best, tied, top = SelfConsistencyDecision._pick_from_scores(cands, scores)
        # 并列 0 分时选最快（c2 exec_time=0.1）
        self.assertEqual(best, "c2")
        self.assertTrue(tied)
        self.assertEqual(top, 0)


if __name__ == "__main__":
    unittest.main()
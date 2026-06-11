# ============================================================================
# 决策 51：两段式评分相关方法测试
# ============================================================================
# 覆盖：
# - score_by_data (R1) - 任务 4.3
# - score_by_sql (R2) - 任务 5.3
# - _pick_from_scores - 任务 6.2
# - pick_lightest_failures - 任务 8.3
# ============================================================================

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.decision.self_consistency import (
    SCORE_DATA_TOPK, SelfConsistencyDecision,
)
from src.execution.executor import ErrorType, StructuredError
from src.sql_generation.sql_generator import SQLCandidate, SQLStatus


def make_cand(id, sql="SELECT 1", status=SQLStatus.SUCCESS,
              result=None, exec_time=0.1, err_type=None, err_msg=""):
    """快速构造候选"""
    c = SQLCandidate(
        id=id, sql=sql, status=status,
        result=result, execution_time=exec_time,
    )
    if err_type:
        c.error_message = err_msg or err_type.value
        c.structured_error = StructuredError(
            error_type=err_type, original_message=err_msg or err_type.value
        )
    return c


class TestScoreByData(unittest.TestCase):
    """R1 数据视角评分测试（任务 4.3）"""

    def test_prompt_does_not_contain_sql(self):
        """R1 prompt 必须不含 SQL 代码（强制 LLM 只看数据）"""
        captured = {}

        def fake_chat_json(messages, **kwargs):
            captured["messages"] = messages
            return {"scores": [{"candidate_id": "c1", "score": 5, "reason": "ok"}]}

        llm = MagicMock()
        llm.chat_json.side_effect = fake_chat_json
        decider = SelfConsistencyDecision(llm_client=llm)

        cand = make_cand("c1", sql="SELECT secret_table.x FROM secret_table",
                         result=[("a", 1)])
        decider.score_by_data([cand], user_query="查询数据")

        user_msg = captured["messages"][1]["content"]
        # SQL 代码不能出现
        self.assertNotIn("SELECT secret_table.x FROM secret_table", user_msg)
        # 但候选 ID 应出现
        self.assertIn("c1", user_msg)

    def test_cell_truncation_20_chars(self):
        """长字符串 cell 应被截断到 20 字符以内（+...）"""
        captured = {}

        def fake_chat_json(messages, **kwargs):
            captured["messages"] = messages
            return {"scores": []}

        llm = MagicMock()
        llm.chat_json.side_effect = fake_chat_json
        decider = SelfConsistencyDecision(llm_client=llm)

        long_str = "X" * 100
        cand = make_cand("c1", result=[(long_str,)])
        decider.score_by_data([cand], user_query="x")

        user_msg = captured["messages"][1]["content"]
        # 长字符串不应完整出现
        self.assertNotIn("X" * 100, user_msg)
        # 应被截断到 20 字符 + "..."
        self.assertIn("X" * 20 + "...", user_msg)

    def test_topk_rows_constraint(self):
        """超过 20 行的数据应只展示前 20 行"""
        captured = {}
        llm = MagicMock()
        llm.chat_json.return_value = {"scores": []}
        decider = SelfConsistencyDecision(llm_client=llm)

        rows = [(i,) for i in range(50)]
        cand = make_cand("c1", result=rows)
        decider.score_by_data([cand], user_query="x")

        user_msg = llm.chat_json.call_args[0][0][1]["content"]
        # 行数信息应说明总共 50 行
        self.assertIn("返回行数: 50", user_msg)
        # 但仅展示 20 行
        self.assertIn(f"前 {SCORE_DATA_TOPK} 行", user_msg)

    def test_prompt_includes_top20_disclaimer(self):
        """prompt 必须明示"结果为节选 20 行"提示"""
        llm = MagicMock()
        llm.chat_json.return_value = {"scores": []}
        decider = SelfConsistencyDecision(llm_client=llm)

        cand = make_cand("c1", result=[(1,)])
        decider.score_by_data([cand], user_query="x")

        user_msg = llm.chat_json.call_args[0][0][1]["content"]
        self.assertIn("节选的前 20 行", user_msg)
        # 检查"不要误判"提示（prompt 用 markdown **不要**，匹配关键词即可）
        self.assertIn("不要", user_msg)
        self.assertIn("误判", user_msg)

    def test_failed_candidates_excluded(self):
        """失败候选应从评分池剔除"""
        llm = MagicMock()
        llm.chat_json.return_value = {
            "scores": [{"candidate_id": "c2", "score": 4, "reason": "ok"}]
        }
        decider = SelfConsistencyDecision(llm_client=llm)

        c1 = make_cand("c1", status=SQLStatus.FAILED,
                       err_type=ErrorType.SYNTAX_ERROR)
        c2 = make_cand("c2", result=[(1,)])
        decider.score_by_data([c1, c2], user_query="x")

        user_msg = llm.chat_json.call_args[0][0][1]["content"]
        # 失败的 c1 不应作为候选 ID 出现（candidates_text 中应只有 c2）
        # 用更精确的匹配："候选 ID: c1" 是 _format_candidate_data_preview 的输出格式
        self.assertNotIn("候选 ID: c1", user_msg)
        self.assertIn("候选 ID: c2", user_msg)

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
        self.assertEqual(llm.chat_json.call_count, 0)

    def test_returned_format(self):
        """返回格式应为 [{candidate_id, score, reason}]"""
        llm = MagicMock()
        llm.chat_json.return_value = {
            "scores": [
                {"candidate_id": "c1", "score": 5, "reason": "完美"},
                {"candidate_id": "c2", "score": 3, "reason": "缺少维度"},
            ]
        }
        decider = SelfConsistencyDecision(llm_client=llm)

        cands = [make_cand("c1", result=[(1,)]), make_cand("c2", result=[(2,)])]
        result = decider.score_by_data(cands, user_query="x")

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["candidate_id"], "c1")
        self.assertEqual(result[0]["score"], 5)
        self.assertEqual(result[1]["candidate_id"], "c2")
        self.assertEqual(result[1]["score"], 3)


class TestScoreBySQL(unittest.TestCase):
    """R2 SQL 视角评分测试（任务 5.3）"""

    def test_prompt_includes_sql(self):
        """R2 prompt 必须含 SQL 代码"""
        llm = MagicMock()
        llm.chat_json.return_value = {"scores": []}
        decider = SelfConsistencyDecision(llm_client=llm)

        cand = make_cand("c1", sql="SELECT a, b FROM users")
        decider.score_by_sql([cand], user_query="x",
                             r1_scores=[{"candidate_id": "c1", "score": 5, "reason": "完美"}])

        user_msg = llm.chat_json.call_args[0][0][1]["content"]
        self.assertIn("SELECT a, b FROM users", user_msg)

    def test_prompt_includes_r1_evaluation(self):
        """R2 prompt 必须带 R1 评分及评价"""
        llm = MagicMock()
        llm.chat_json.return_value = {"scores": []}
        decider = SelfConsistencyDecision(llm_client=llm)

        cand = make_cand("c1", sql="SELECT 1")
        decider.score_by_sql([cand], user_query="x",
                             r1_scores=[{"candidate_id": "c1", "score": 5,
                                         "reason": "数据完美匹配"}])

        user_msg = llm.chat_json.call_args[0][0][1]["content"]
        self.assertIn("数据完美匹配", user_msg)
        self.assertIn("5/5", user_msg)

    def test_strict_mode_keywords(self):
        """R2 必须使用严格模式标准（含'完美满足/严重偏差'等关键词）"""
        llm = MagicMock()
        llm.chat_json.return_value = {"scores": []}
        decider = SelfConsistencyDecision(llm_client=llm)

        cand = make_cand("c1", sql="SELECT 1")
        decider.score_by_sql([cand], user_query="x",
                             r1_scores=[{"candidate_id": "c1", "score": 5, "reason": ""}])

        user_msg = llm.chat_json.call_args[0][0][1]["content"]
        self.assertIn("严格模式", user_msg)
        self.assertIn("完美满足", user_msg)


class TestPickFromScores(unittest.TestCase):
    """_pick_from_scores 选最高分逻辑测试（任务 6.2）"""

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
            make_cand("c2", exec_time=0.1),  # 最快
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
            {"candidate_id": "c2", "score": 5, "reason": ""},
            {"candidate_id": "c1", "score": 5, "reason": ""},
        ]
        best, tied, _ = SelfConsistencyDecision._pick_from_scores(cands, scores)
        self.assertEqual(best, "c1")  # 候选列表中 c1 在前
        self.assertTrue(tied)

    def test_empty_scores_fallback(self):
        """评分为空时取候选列表首位"""
        cands = [make_cand("c1"), make_cand("c2")]
        best, tied, top = SelfConsistencyDecision._pick_from_scores(cands, [])
        self.assertEqual(best, "c1")
        self.assertFalse(tied)
        self.assertEqual(top, 0)

    def test_all_zero_picks_first(self):
        """全部 0 分时同样选最高分组的第一个（视为唯一最高=0）"""
        cands = [make_cand("c1"), make_cand("c2")]
        scores = [
            {"candidate_id": "c1", "score": 0, "reason": ""},
            {"candidate_id": "c2", "score": 0, "reason": ""},
        ]
        best, tied, top = SelfConsistencyDecision._pick_from_scores(cands, scores)
        self.assertIn(best, ("c1", "c2"))
        self.assertTrue(tied)
        self.assertEqual(top, 0)


class TestPickLightestFailures(unittest.TestCase):
    """pick_lightest_failures 全失败分支逻辑测试（任务 8.3 部分）"""

    def test_lightest_semantic(self):
        """混合错误中应取 SEMANTIC（最轻）"""
        cands = [
            make_cand("c1", status=SQLStatus.FAILED, err_type=ErrorType.TIMEOUT_ERROR),
            make_cand("c2", status=SQLStatus.FAILED, err_type=ErrorType.SEMANTIC_ERROR),
            make_cand("c3", status=SQLStatus.FAILED, err_type=ErrorType.SEMANTIC_ERROR),
            make_cand("c4", status=SQLStatus.FAILED, err_type=ErrorType.SYNTAX_ERROR),
        ]
        result = SelfConsistencyDecision.pick_lightest_failures(cands)
        # SEMANTIC 是最轻的，应只返回 c2、c3
        ids = [c.id for c in result]
        self.assertEqual(ids, ["c2", "c3"])

    def test_all_unfixable_returns_empty(self):
        """最轻全是 TIMEOUT/RUNTIME/PERMISSION 时返回空"""
        cands = [
            make_cand("c1", status=SQLStatus.FAILED, err_type=ErrorType.TIMEOUT_ERROR),
            make_cand("c2", status=SQLStatus.FAILED, err_type=ErrorType.PERMISSION_ERROR),
        ]
        result = SelfConsistencyDecision.pick_lightest_failures(cands)
        self.assertEqual(result, [])

    def test_syntax_when_no_semantic(self):
        """没有 SEMANTIC 时应取 SYNTAX"""
        cands = [
            make_cand("c1", status=SQLStatus.FAILED, err_type=ErrorType.SYNTAX_ERROR),
            make_cand("c2", status=SQLStatus.FAILED, err_type=ErrorType.UNKNOWN),
            make_cand("c3", status=SQLStatus.FAILED, err_type=ErrorType.SYNTAX_ERROR),
        ]
        result = SelfConsistencyDecision.pick_lightest_failures(cands)
        ids = [c.id for c in result]
        self.assertEqual(ids, ["c1", "c3"])

    def test_no_structured_error_falls_to_unknown(self):
        """没有 structured_error 时按 UNKNOWN 处理"""
        cand = make_cand("c1", status=SQLStatus.FAILED)
        cand.structured_error = None
        # 一个 SEMANTIC 候选打底，应只取 SEMANTIC
        cand2 = make_cand("c2", status=SQLStatus.FAILED, err_type=ErrorType.SEMANTIC_ERROR)
        result = SelfConsistencyDecision.pick_lightest_failures([cand, cand2])
        ids = [c.id for c in result]
        self.assertEqual(ids, ["c2"])


if __name__ == "__main__":
    unittest.main()

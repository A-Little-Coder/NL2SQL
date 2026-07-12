# ============================================================================
# Rewrite 模块单元测试（v2 设计）
# ============================================================================
# 运行: pytest tests/graph/test_rewrite.py -v
# ============================================================================

import json
import unittest
from unittest.mock import MagicMock

from src.rewrite.pre_reject import make_pre_reject_node, _detect_write_operation
from src.rewrite.rewrite_subgraph import (
    make_detect_issues_node,
    make_rewrite_execute_node,
    make_clarify_node,
    _format_history_lines,
)


def _make_llm_mock(json_dict: dict):
    """构造一个 mock LLM，stream 返回单 chunk JSON。"""
    mock = MagicMock()
    mock.stream.return_value = [(json.dumps(json_dict, ensure_ascii=False), None)]
    return mock


def _make_llm_mock_sequence(json_dicts: list):
    """构造一个 mock LLM，按顺序返回多个 JSON。"""
    mock = MagicMock()
    mock.stream.side_effect = [
        [(json.dumps(jd, ensure_ascii=False), None)]
        for jd in json_dicts
    ]
    return mock


# ============================================================================
# 前置拒答检测测试
# ============================================================================
class TestPreRejectWriteOperationDetection(unittest.TestCase):
    """写操作硬性检测"""

    def test_delete_sql_keyword(self):
        self.assertTrue(_detect_write_operation("delete from users"))

    def test_drop_keyword(self):
        self.assertTrue(_detect_write_operation("drop table orders"))

    def test_chinese_delete(self):
        self.assertTrue(_detect_write_operation("帮我把这条记录删除"))

    def test_chinese_update(self):
        self.assertTrue(_detect_write_operation("更新一下这个订单的状态"))

    def test_normal_query_not_write(self):
        self.assertFalse(_detect_write_operation("查询苹果的销售额"))

    def test_column_name_not_write(self):
        """updated_at 列名不应误判为写操作（词边界）"""
        self.assertFalse(_detect_write_operation("查 updated_at 字段"))

    def test_created_at_not_write(self):
        self.assertFalse(_detect_write_operation("按 created_at 排序"))


class TestPreRejectNode(unittest.TestCase):
    """前置拒答检测节点"""

    def test_empty_query_rejected(self):
        node = make_pre_reject_node()
        result = node({"user_query": "", "trace_log": []})
        self.assertIn("rewrite_rejection_reason", result)
        self.assertIn("空", result["rewrite_rejection_reason"])

    def test_whitespace_query_rejected(self):
        node = make_pre_reject_node()
        result = node({"user_query": "   ", "trace_log": []})
        self.assertIn("rewrite_rejection_reason", result)

    def test_write_operation_rejected(self):
        node = make_pre_reject_node()
        result = node({"user_query": "删除 users 表里所有数据", "trace_log": []})
        self.assertIn("rewrite_rejection_reason", result)
        self.assertIn("写操作", result["rewrite_rejection_reason"])

    def test_normal_query_passes(self):
        node = make_pre_reject_node()
        result = node({"user_query": "查苹果的销售额", "trace_log": []})
        self.assertNotIn("rewrite_rejection_reason", result)
        self.assertNotIn("rejection_reason", result)


# ============================================================================
# 历史格式化测试
# ============================================================================
class TestFormatHistoryLines(unittest.TestCase):
    """会话历史格式化"""

    def test_empty_history(self):
        self.assertEqual(_format_history_lines([]), "（无）")

    def test_none_history(self):
        self.assertEqual(_format_history_lines(None), "（无）")

    def test_session_turn_format(self):
        """session turn 格式（user_query 字段）"""
        history = [{"user_query": "查苹果销售额", "turn_index": 1}]
        result = _format_history_lines(history)
        self.assertIn("查苹果销售额", result)
        self.assertIn("[user]", result)

    def test_standard_message_format(self):
        """标准消息格式（role/content 字段）"""
        history = [{"role": "user", "content": "查苹果销售额"}]
        result = _format_history_lines(history)
        self.assertIn("查苹果销售额", result)
        self.assertIn("[user]", result)

    def test_rejection_turn(self):
        """拒答轮次标记"""
        history = [{"user_query": "删除数据", "rejection_reason": "写操作"}]
        result = _format_history_lines(history)
        self.assertIn("被拒答", result)

    def test_max_5_turns(self):
        history = [{"user_query": f"q{i}", "turn_index": i} for i in range(10)]
        result = _format_history_lines(history)
        self.assertEqual(result.count("[user]"), 5)  # 最多 5 轮


# ============================================================================
# 问题检测子节点测试
# ============================================================================
class TestDetectIssuesNode(unittest.TestCase):
    """问题检测子节点"""

    def test_no_llm_degrades_to_no_issues(self):
        node = make_detect_issues_node(llm_client=None)
        state = {"user_query": "查苹果的销售额", "conversation_history": []}
        result = node(state)
        self.assertFalse(result["has_issues"])

    def test_llm_returns_no_issues(self):
        mock_llm = _make_llm_mock({"has_issues": False, "issue_detail": "", "issue_types": []})
        node = make_detect_issues_node(llm_client=mock_llm)
        state = {"user_query": "查苹果的销售额", "conversation_history": []}
        result = node(state)
        self.assertFalse(result["has_issues"])

    def test_llm_returns_has_issues(self):
        mock_llm = _make_llm_mock({
            "has_issues": True,
            "issue_detail": "查询含指代'那'",
            "issue_types": ["指代"],
        })
        node = make_detect_issues_node(llm_client=mock_llm)
        state = {"user_query": "那去年的呢", "conversation_history": []}
        result = node(state)
        self.assertTrue(result["has_issues"])
        self.assertIn("指代", result["issue_types"])

    def test_llm_exception_degrades(self):
        mock_llm = MagicMock()
        mock_llm.stream.side_effect = Exception("LLM 失败")
        node = make_detect_issues_node(llm_client=mock_llm)
        state = {"user_query": "查苹果的销售额", "conversation_history": []}
        result = node(state)
        self.assertFalse(result["has_issues"])


# ============================================================================
# 改写执行子节点测试
# ============================================================================
class TestRewriteExecuteNode(unittest.TestCase):
    """改写执行子节点"""

    def test_no_llm_passthrough(self):
        node = make_rewrite_execute_node(llm_client=None)
        state = {"user_query": "查苹果的销售额", "conversation_history": [], "clarify_context": ""}
        result = node(state)
        self.assertEqual(result["rewritten_query"], "查苹果的销售额")

    def test_llm_rewrites(self):
        mock_llm = _make_llm_mock({
            "rewritten_query": "查苹果公司2022年的销售额",
            "rewrite_reason": "补全指代：'那'指苹果公司",
        })
        node = make_rewrite_execute_node(llm_client=mock_llm)
        state = {"user_query": "那去年的呢", "conversation_history": [], "clarify_context": ""}
        result = node(state)
        self.assertEqual(result["rewritten_query"], "查苹果公司2022年的销售额")
        self.assertEqual(result["rewrite_reason"], "补全指代：'那'指苹果公司")

    def test_llm_exception_passthrough(self):
        mock_llm = MagicMock()
        mock_llm.stream.side_effect = Exception("LLM 失败")
        node = make_rewrite_execute_node(llm_client=mock_llm)
        state = {"user_query": "查苹果的销售额", "conversation_history": [], "clarify_context": ""}
        result = node(state)
        self.assertEqual(result["rewritten_query"], "查苹果的销售额")


# ============================================================================
# 反问澄清子节点测试
# ============================================================================
class TestClarifyNode(unittest.TestCase):
    """反问澄清子节点（无 interrupt 时降级）"""

    def test_no_interrupt_degrades(self):
        node = make_clarify_node()
        state = {"user_query": "那去年的呢", "conversation_history": [], "clarify_round": 0}
        result = node(state)
        # 无 interrupt 时降级，返回空 clarify_context
        self.assertEqual(result["clarify_context"], "")


if __name__ == "__main__":
    unittest.main()
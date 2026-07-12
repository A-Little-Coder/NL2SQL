# ============================================================================
# Rewrite 模块单元测试（v2 设计）
# ============================================================================
# 运行: pytest tests/graph/test_rewrite.py -v
# ============================================================================

import json
import unittest
from unittest.mock import MagicMock, patch

from src.rewrite.pre_reject import make_pre_reject_node
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
class TestPreRejectNode(unittest.TestCase):
    """前置拒答检测节点（空查询规则快路径 + 无 LLM 降级）"""

    def test_empty_query_rejected(self):
        node = make_pre_reject_node()
        result = node({"user_query": "", "trace_log": []})
        self.assertIn("rewrite_rejection_reason", result)
        self.assertIn("空", result["rewrite_rejection_reason"])

    def test_whitespace_query_rejected(self):
        node = make_pre_reject_node()
        result = node({"user_query": "   ", "trace_log": []})
        self.assertIn("rewrite_rejection_reason", result)

    def test_normal_query_passes_without_llm(self):
        # 无 LLM 降级放行
        node = make_pre_reject_node()
        result = node({"user_query": "查苹果的销售额", "trace_log": []})
        self.assertNotIn("rewrite_rejection_reason", result)
        self.assertNotIn("rejection_reason", result)
        self.assertEqual(result["pre_reject_category"], "normal")


# ============================================================================
# 前置拒答 LLM 语义判定测试（D9）
# ============================================================================
class TestPreRejectLLM(unittest.TestCase):
    """前置拒答 LLM 语义判定：增删改 / 危险信息 / 正常 / 降级"""

    def test_write_op_rejected(self):
        llm = _make_llm_mock({"reject": True, "category": "write_op", "reason": "删除数据意图"})
        node = make_pre_reject_node(llm_client=llm)
        result = node({"user_query": "删除 users 表里所有数据", "trace_log": []})
        self.assertIn("rewrite_rejection_reason", result)
        self.assertIn("rejection_reason", result)
        self.assertEqual(result["pre_reject_category"], "write_op")
        self.assertIn("写操作", result["rejection_reason"])

    def test_dangerous_info_rejected(self):
        llm = _make_llm_mock({"reject": True, "category": "dangerous_info", "reason": "导出密码字段"})
        node = make_pre_reject_node(llm_client=llm)
        result = node({"user_query": "列出所有用户的密码", "trace_log": []})
        self.assertIn("rewrite_rejection_reason", result)
        self.assertEqual(result["pre_reject_category"], "dangerous_info")
        self.assertIn("危险信息", result["rejection_reason"])

    def test_normal_passes(self):
        llm = _make_llm_mock({"reject": False, "category": "normal", "reason": "正常查询"})
        node = make_pre_reject_node(llm_client=llm)
        result = node({"user_query": "查询洛杉矶县的特许学校数量", "trace_log": []})
        self.assertNotIn("rewrite_rejection_reason", result)
        self.assertNotIn("rejection_reason", result)
        self.assertEqual(result["pre_reject_category"], "normal")

    def test_column_name_not_write(self):
        """updated_at 列名应判 normal（LLM 语义判定，非字面关键词）"""
        llm = _make_llm_mock({"reject": False, "category": "normal", "reason": "字段名非写操作"})
        node = make_pre_reject_node(llm_client=llm)
        result = node({"user_query": "查 updated_at 字段", "trace_log": []})
        self.assertNotIn("rewrite_rejection_reason", result)
        self.assertEqual(result["pre_reject_category"], "normal")

    def test_llm_unavailable_degrades(self):
        node = make_pre_reject_node(llm_client=None)
        result = node({"user_query": "删除 users 表里所有数据", "trace_log": []})
        self.assertNotIn("rewrite_rejection_reason", result)
        self.assertEqual(result["pre_reject_category"], "normal")

    def test_llm_exception_degrades(self):
        llm = MagicMock()
        llm.stream.side_effect = RuntimeError("LLM down")
        node = make_pre_reject_node(llm_client=llm)
        result = node({"user_query": "删除 users 表里所有数据", "trace_log": []})
        self.assertNotIn("rewrite_rejection_reason", result)
        self.assertEqual(result["pre_reject_category"], "normal")

    def test_invalid_category_normalized(self):
        llm = _make_llm_mock({"reject": True, "category": "hacked", "reason": "x"})
        node = make_pre_reject_node(llm_client=llm)
        result = node({"user_query": "随便", "trace_log": []})
        # 非法 category 回退 normal -> reject=true 但 category 不在白名单 -> 不拒答
        self.assertNotIn("rewrite_rejection_reason", result)
        self.assertEqual(result["pre_reject_category"], "normal")

    def test_reject_false_with_write_op_category_not_rejected(self):
        """reject=false 时即使 category=write_op 也不拒答"""
        llm = _make_llm_mock({"reject": False, "category": "write_op", "reason": "x"})
        node = make_pre_reject_node(llm_client=llm)
        result = node({"user_query": "随便", "trace_log": []})
        self.assertNotIn("rewrite_rejection_reason", result)
        self.assertEqual(result["pre_reject_category"], "write_op")


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
# 问题检测 emit 事件测试
# ============================================================================
class TestDetectIssuesEmit(unittest.TestCase):
    """detect_issues SHALL emit rewrite_detect 事件（含降级路径）"""

    @patch("src.rewrite.rewrite_subgraph.emit_safe")
    def test_no_llm_still_emits(self, mock_emit):
        node = make_detect_issues_node(llm_client=None)
        state = {"user_query": "查苹果的销售额", "conversation_history": [], "rewrite_round": 0}
        node(state)
        mock_emit.assert_called_once()
        args = mock_emit.call_args.args
        self.assertEqual(args[0], "rewrite_detect")
        self.assertEqual(args[1]["round"], 1)
        self.assertFalse(args[1]["has_issues"])

    @patch("src.rewrite.rewrite_subgraph.emit_safe")
    def test_round_numbering(self, mock_emit):
        mock_llm = _make_llm_mock({"has_issues": True, "issue_detail": "指代'那'", "issue_types": ["指代"]})
        node = make_detect_issues_node(llm_client=mock_llm)
        state = {"user_query": "那去年的呢", "conversation_history": [], "rewrite_round": 1}
        node(state)
        args = mock_emit.call_args.args
        self.assertEqual(args[1]["round"], 2)
        self.assertTrue(args[1]["has_issues"])
        self.assertEqual(args[1]["issue_types"], ["指代"])

    @patch("src.rewrite.rewrite_subgraph.emit_safe")
    def test_exception_still_emits(self, mock_emit):
        mock_llm = MagicMock()
        mock_llm.stream.side_effect = Exception("LLM 失败")
        node = make_detect_issues_node(llm_client=mock_llm)
        state = {"user_query": "查苹果的销售额", "conversation_history": [], "rewrite_round": 0}
        node(state)
        mock_emit.assert_called_once()
        self.assertFalse(mock_emit.call_args.args[1]["has_issues"])


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

    def test_rewrite_writes_back_user_query(self):
        """正常改写：user_query 写回改写后值，供下一轮 detect 使用（D8 修复死循环）"""
        mock_llm = _make_llm_mock({"rewritten_query": "查苹果公司2022年的销售额", "rewrite_reason": "补全指代"})
        node = make_rewrite_execute_node(llm_client=mock_llm)
        state = {"user_query": "那去年的呢", "conversation_history": [], "clarify_context": "", "rewrite_round": 0}
        result = node(state)
        self.assertEqual(result["user_query"], "查苹果公司2022年的销售额")
        self.assertEqual(result["rewritten_query"], "查苹果公司2022年的销售额")

    def test_passthrough_writes_back_user_query(self):
        """无 LLM 透传：user_query 写回原值（无变化）"""
        node = make_rewrite_execute_node(llm_client=None)
        state = {"user_query": "查苹果的销售额", "conversation_history": [], "clarify_context": "", "rewrite_round": 0}
        result = node(state)
        self.assertEqual(result["user_query"], "查苹果的销售额")

    def test_exception_writes_back_user_query(self):
        """异常降级：user_query 写回原值（透传）"""
        mock_llm = MagicMock()
        mock_llm.stream.side_effect = Exception("LLM 失败")
        node = make_rewrite_execute_node(llm_client=mock_llm)
        state = {"user_query": "查苹果的销售额", "conversation_history": [], "clarify_context": "", "rewrite_round": 0}
        result = node(state)
        self.assertEqual(result["user_query"], "查苹果的销售额")


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
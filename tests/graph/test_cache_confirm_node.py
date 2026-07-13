# ============================================================================
# CacheConfirm 节点单元测试（harden-history-cache）
# ============================================================================
# 验证：
#   - cache_confirm_approved 预置非 None 时跳过 interrupt
#   - 用户选择复用 → cache_confirm_approved=True，保持 cache_hit
#   - 用户选择重新生成 → cache_confirm_approved=False，置 cache_hit=False + cached_sql=None
#   - interrupt payload 形状兼容 query.py clarification 处理
#   - SQL 超 5 行或 200 字符时截断
#
# 运行: pytest tests/graph/test_cache_confirm_node.py -v
# ============================================================================

import unittest
from unittest.mock import MagicMock, patch

from src.graph.main_graph import make_cache_confirm_node


class TestCacheConfirmNode(unittest.TestCase):
    """CacheConfirm 节点测试"""

    def setUp(self):
        """测试前置：mock emit_safe。"""
        patcher = patch('src.graph.main_graph.emit_safe')
        self.mock_emit = patcher.start()
        self.addCleanup(patcher.stop)
        # 每次测试都创建一个新的 mock interrupt
        patcher_interrupt = patch('src.graph.main_graph.interrupt')
        self.mock_interrupt = patcher_interrupt.start()
        self.addCleanup(patcher_interrupt.stop)

    def test_preapproved_yes_skips_interrupt(self):
        """cache_confirm_approved=True 预置：跳过 interrupt，直接返回 approved=True。"""
        node = make_cache_confirm_node()
        out = node({
            "user_query": "查销售额",
            "cache_hit": True,
            "cached_sql": "SELECT 1",
            "cache_confirm_approved": True  # 预置
        })
        self.assertTrue(out.get("cache_confirm_approved"))
        self.assertNotIn("cache_hit", out)  # 不修改 cache_hit（保持 True）
        self.assertNotIn("cached_sql", out)  # 不清空 cached_sql
        # 验证 interrupt 没被调用
        self.assertEqual(self.mock_interrupt.call_count, 0)

    def test_preapproved_no_skips_interrupt(self):
        """cache_confirm_approved=False 预置：跳过 interrupt，返回 approved=False + cache_hit=False + cached_sql=None。"""
        node = make_cache_confirm_node()
        out = node({
            "user_query": "查销售额",
            "cache_hit": True,
            "cached_sql": "SELECT 1",
            "cache_confirm_approved": False  # 预置
        })
        self.assertFalse(out.get("cache_confirm_approved"))
        self.assertFalse(out.get("cache_hit"))
        self.assertIsNone(out.get("cached_sql"))
        # 验证 interrupt 没被调用
        self.assertEqual(self.mock_interrupt.call_count, 0)

    def test_interrupt_payload_shape(self):
        """interrupt payload 形状：{question, ambiguities, round}，兼容 query.py。"""
        self.mock_interrupt.return_value = "复用"
        node = make_cache_confirm_node()
        node({
            "user_query": "查今年销售额",
            "cached_historical_query": "查去年销售额",
            "cached_sql": "SELECT sales FROM t WHERE year=2024",
            "adjusted_cached_sql": "SELECT sales FROM t WHERE year=2025"
        })
        # 验证 interrupt 被调用一次，payload 形状正确
        self.mock_interrupt.assert_called_once()
        payload = self.mock_interrupt.call_args[0][0]
        self.assertIn("question", payload)
        self.assertIn("ambiguities", payload)
        self.assertIn("round", payload)
        self.assertEqual(payload["ambiguities"], [])
        self.assertEqual(payload["round"], 1)
        # question 包含关键信息
        self.assertIn("历史相似查询", payload["question"])
        self.assertIn("查今年销售额", payload["question"])
        self.assertIn("查去年销售额", payload["question"])
        self.assertIn("2025", payload["question"])  # adjusted_cached_sql

    def test_user_approves_keeps_cache_hit(self):
        """用户选择复用（多种同义词）：保持 cache_hit。"""
        # 测试各种肯定回答
        for yes_answer in ["复用", "reuse", "yes", "是", "1", "y", "确认", "Y", "YES"]:
            self.mock_interrupt.return_value = yes_answer
            node = make_cache_confirm_node()
            out = node({
                "user_query": "q",
                "cache_hit": True,
                "cached_sql": "SELECT 1"
            })
            self.assertTrue(out.get("cache_confirm_approved"))
            self.assertNotIn("cache_hit", out)  # 保持 True（不修改）

    def test_user_rejects_resets_cache(self):
        """用户选择重新生成：置 cache_hit=False + cached_sql=None。"""
        self.mock_interrupt.return_value = "重新生成"
        node = make_cache_confirm_node()
        out = node({
            "user_query": "q",
            "cache_hit": True,
            "cached_sql": "SELECT 1"
        })
        self.assertFalse(out.get("cache_confirm_approved"))
        self.assertFalse(out.get("cache_hit"))
        self.assertIsNone(out.get("cached_sql"))

    def test_sql_truncated_long(self):
        """SQL 超 200 字符：截断显示。"""
        self.mock_interrupt.return_value = "复用"
        long_sql = "SELECT " + "a" * 100 + ", " + "b" * 100 + " FROM t"  # 远超 200
        node = make_cache_confirm_node()
        node({
            "user_query": "q",
            "cached_historical_query": "old q",
            "cached_sql": long_sql
        })
        payload = self.mock_interrupt.call_args[0][0]
        self.assertIn("截断", payload["question"])

    def test_sql_truncated_multiline(self):
        """SQL 超 5 行：截断显示。"""
        self.mock_interrupt.return_value = "复用"
        multiline_sql = "SELECT 1\nUNION\nSELECT 2\nUNION\nSELECT 3\nUNION\nSELECT 4\nUNION\nSELECT 5\nUNION\nSELECT 6"  # 6 行
        node = make_cache_confirm_node()
        node({
            "user_query": "q",
            "cached_historical_query": "old q",
            "cached_sql": multiline_sql
        })
        payload = self.mock_interrupt.call_args[0][0]
        self.assertIn("截断", payload["question"])

    def test_emit_safe_fired(self):
        """emit_safe 发出 cache_confirm 事件。"""
        self.mock_interrupt.return_value = "复用"
        node = make_cache_confirm_node()
        node({
            "user_query": "查销售额",
            "cached_historical_query": "查去年销售额"
        })
        self.mock_emit.assert_called_once()
        event_name, event_payload = self.mock_emit.call_args[0]
        self.assertEqual(event_name, "cache_confirm")
        self.assertTrue(event_payload["approved"])
        self.assertEqual(event_payload["user_choice"], "复用")

    def test_trace_log_updated(self):
        """trace_log 被正确追加。"""
        self.mock_interrupt.return_value = "复用"
        node = make_cache_confirm_node()
        out = node({
            "user_query": "q",
            "cached_sql": "SELECT 1",
            "trace_log": ["prev log"]
        })
        self.assertEqual(out.get("trace_log", [])[0], "prev log")
        self.assertIn("CacheConfirm", out.get("trace_log", [])[1])

    # ---- change clarify-choice-inspector-cancel：结构化 kind/options + yes/no ----

    def test_payload_has_kind_and_options(self):
        """interrupt payload 含 kind=confirm 与 2 项 options（是/否）。"""
        self.mock_interrupt.return_value = "yes"
        node = make_cache_confirm_node()
        node({"user_query": "q", "cached_sql": "SELECT 1"})
        payload = self.mock_interrupt.call_args[0][0]
        self.assertEqual(payload["kind"], "confirm")
        self.assertEqual(len(payload["options"]), 2)
        values = [o["value"] for o in payload["options"]]
        self.assertIn("yes", values)
        self.assertIn("no", values)

    def test_user_choice_yes_approves(self):
        """user_choice="yes" -> approved=True（结构化 value 优先）。"""
        self.mock_interrupt.return_value = "yes"
        node = make_cache_confirm_node()
        out = node({"user_query": "q", "cache_hit": True, "cached_sql": "SELECT 1"})
        self.assertTrue(out["cache_confirm_approved"])

    def test_user_choice_no_rejects(self):
        """user_choice="no" -> approved=False + cache_hit=False + cached_sql=None。"""
        self.mock_interrupt.return_value = "no"
        node = make_cache_confirm_node()
        out = node({"user_query": "q", "cache_hit": True, "cached_sql": "SELECT 1"})
        self.assertFalse(out["cache_confirm_approved"])
        self.assertFalse(out["cache_hit"])
        self.assertIsNone(out["cached_sql"])

    def test_non_standard_value_falls_back_to_set_match(self):
        """user_choice="是"（非 yes/no）-> 回退字符串集合匹配，approved=True（兼容旧前端）。"""
        self.mock_interrupt.return_value = "是"
        node = make_cache_confirm_node()
        out = node({"user_query": "q", "cache_hit": True, "cached_sql": "SELECT 1"})
        self.assertTrue(out["cache_confirm_approved"])


if __name__ == "__main__":
    unittest.main()

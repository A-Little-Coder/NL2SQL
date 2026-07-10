# ============================================================================
# ValueRewrite 节点单元测试（harden-history-cache）
# ============================================================================
# 验证：
#   - 有 llm_client 时调用 VALUE_REWRITE_PROMPT，解析 adjusted_sql
#   - 无 llm_client 或无 historical_query 或异常时透传 cached_sql
#   - 支持 WHERE 值变化/LIMIT 值变化/HAVING 值变化/多值同变
#   - trace_log 与 emit_safe 事件正确输出
#
# 运行: pytest tests/graph/test_value_rewrite_node.py -v
# ============================================================================

import unittest
from unittest.mock import MagicMock, patch

from src.graph.main_graph import make_value_rewrite_node


class TestValueRewriteNode(unittest.TestCase):
    """ValueRewrite 节点测试"""

    def setUp(self):
        """测试前置：mock emit_safe 防止真正发送事件。"""
        patcher = patch('src.graph.main_graph.emit_safe')
        self.mock_emit = patcher.start()
        self.addCleanup(patcher.stop)

    def test_no_cached_sql_returns_none(self):
        """无 cached_sql：返回 adjusted_cached_sql=None。"""
        node = make_value_rewrite_node(llm_client=None)
        out = node({
            "user_query": "查销售额",
            "cached_sql": "",
            "cached_historical_query": "查去年销售额"
        })
        self.assertIsNone(out.get("adjusted_cached_sql"))
        self.assertIn("ValueRewrite", out.get("trace_log", [""])[-1])

    def test_no_historical_query_passes_through(self):
        """无 historical_query：透传 cached_sql。"""
        node = make_value_rewrite_node(llm_client=MagicMock())
        out = node({
            "user_query": "查销售额",
            "cached_sql": "SELECT sales FROM t",
            "cached_historical_query": None
        })
        self.assertEqual(out.get("adjusted_cached_sql"), "SELECT sales FROM t")

    def test_no_llm_client_passes_through(self):
        """无 llm_client：透传 cached_sql。"""
        node = make_value_rewrite_node(llm_client=None)
        out = node({
            "user_query": "查今年销售额",
            "cached_sql": "SELECT sales FROM t WHERE year=2024",
            "cached_historical_query": "查去年销售额"
        })
        self.assertEqual(out.get("adjusted_cached_sql"), "SELECT sales FROM t WHERE year=2024")

    def test_llm_rewrites_where_region_value(self):
        """WHERE 地区值变化：调用 VALUE_REWRITE_PROMPT 解析 adjusted_sql。"""
        llm_client = MagicMock()
        llm_client.invoke.return_value = {
            "adjusted_sql": "SELECT SUM(amt) FROM orders WHERE region='华北'",
            "changed": True,
            "reason": "region '华东' -> '华北'"
        }
        node = make_value_rewrite_node(llm_client=llm_client)

        out = node({
            "user_query": "华北区的销售额",
            "cached_sql": "SELECT SUM(amt) FROM orders WHERE region='华东'",
            "cached_historical_query": "华东区的销售额"
        })

        self.assertEqual(out.get("adjusted_cached_sql"), "SELECT SUM(amt) FROM orders WHERE region='华北'")
        # 验证 llm_client.invoke 被调用
        self.assertTrue(llm_client.invoke.called)
        # 验证 emit_safe 发出 value_rewrite 事件
        self.mock_emit.assert_called_once()
        event_name, event_payload = self.mock_emit.call_args[0]
        self.assertEqual(event_name, "value_rewrite")
        self.assertEqual(event_payload["changed"], True)

    def test_llm_rewrites_limit_value(self):
        """LIMIT 值变化：调用 VALUE_REWRITE_PROMPT 解析 adjusted_sql。"""
        llm_client = MagicMock()
        llm_client.invoke.return_value = {
            "adjusted_sql": "SELECT product, sales FROM orders ORDER BY sales DESC LIMIT 20",
            "changed": True,
            "reason": "LIMIT 10 -> 20"
        }
        node = make_value_rewrite_node(llm_client=llm_client)

        out = node({
            "user_query": "销售额前20的产品",
            "cached_sql": "SELECT product, sales FROM orders ORDER BY sales DESC LIMIT 10",
            "cached_historical_query": "销售额前10的产品"
        })

        self.assertEqual(out.get("adjusted_cached_sql"), "SELECT product, sales FROM orders ORDER BY sales DESC LIMIT 20")

    def test_llm_rewrites_having_value(self):
        """HAVING 值变化：调用 VALUE_REWRITE_PROMPT 解析 adjusted_sql。"""
        llm_client = MagicMock()
        llm_client.invoke.return_value = {
            "adjusted_sql": "SELECT region, SUM(amt) FROM orders GROUP BY region HAVING SUM(amt) > 20000",
            "changed": True,
            "reason": "HAVING SUM > 10000 -> 20000"
        }
        node = make_value_rewrite_node(llm_client=llm_client)

        out = node({
            "user_query": "销售额超过2万的地区",
            "cached_sql": "SELECT region, SUM(amt) FROM orders GROUP BY region HAVING SUM(amt) > 10000",
            "cached_historical_query": "销售额超过1万的地区"
        })

        self.assertEqual(out.get("adjusted_cached_sql"), "SELECT region, SUM(amt) FROM orders GROUP BY region HAVING SUM(amt) > 20000")

    def test_llm_rewrites_multiple_values(self):
        """多值同变：同时改写 WHERE 中的多个值参数。"""
        llm_client = MagicMock()
        llm_client.invoke.return_value = {
            "adjusted_sql": "SELECT SUM(amt) FROM orders WHERE region='华北' AND year=2025",
            "changed": True,
            "reason": "region '华东'->'华北', year 2024->2025"
        }
        node = make_value_rewrite_node(llm_client=llm_client)

        out = node({
            "user_query": "华北区2025年的销售额",
            "cached_sql": "SELECT SUM(amt) FROM orders WHERE region='华东' AND year=2024",
            "cached_historical_query": "华东区2024年的销售额"
        })

        self.assertEqual(out.get("adjusted_cached_sql"), "SELECT SUM(amt) FROM orders WHERE region='华北' AND year=2025")

    def test_llm_no_change_passes_through(self):
        """值一致透传：原样返回 cached_sql。"""
        llm_client = MagicMock()
        llm_client.invoke.return_value = {
            "adjusted_sql": "SELECT SUM(amt) FROM orders WHERE region='华东'",
            "changed": False,
            "reason": "值参数一致无需修改"
        }
        node = make_value_rewrite_node(llm_client=llm_client)

        out = node({
            "user_query": "华东区的销售额",
            "cached_sql": "SELECT SUM(amt) FROM orders WHERE region='华东'",
            "cached_historical_query": "华东区的销售额"
        })

        self.assertEqual(out.get("adjusted_cached_sql"), "SELECT SUM(amt) FROM orders WHERE region='华东'")

    def test_llm_non_literal_value_passes_through(self):
        """值非字面量透传：原样返回 cached_sql（如使用函数表达式）。"""
        llm_client = MagicMock()
        llm_client.invoke.return_value = {
            "adjusted_sql": "SELECT SUM(amt) FROM orders WHERE year=YEAR(CURRENT_DATE)",
            "changed": False,
            "reason": "值非字面量，难以安全改写"
        }
        node = make_value_rewrite_node(llm_client=llm_client)

        out = node({
            "user_query": "今年的销售额",
            "cached_sql": "SELECT SUM(amt) FROM orders WHERE year=YEAR(CURRENT_DATE)",
            "cached_historical_query": "去年的销售额"
        })

        self.assertEqual(out.get("adjusted_cached_sql"), "SELECT SUM(amt) FROM orders WHERE year=YEAR(CURRENT_DATE)")

    def test_llm_exception_falls_back(self):
        """llm_client 调用抛异常：降级透传 cached_sql。"""
        llm_client = MagicMock()
        llm_client.invoke.side_effect = RuntimeError("LLM 挂了")
        node = make_value_rewrite_node(llm_client=llm_client)

        out = node({
            "user_query": "华北区的销售额",
            "cached_sql": "SELECT SUM(amt) FROM orders WHERE region='华东'",
            "cached_historical_query": "华东区的销售额"
        })

        self.assertEqual(out.get("adjusted_cached_sql"), "SELECT SUM(amt) FROM orders WHERE region='华东'")
        # 验证 emit_safe 发出 value_rewrite 事件（changed=False）
        self.mock_emit.assert_called_once()
        event_name, event_payload = self.mock_emit.call_args[0]
        self.assertEqual(event_name, "value_rewrite")
        self.assertIn("异常降级", event_payload["reason"])

    def test_llm_invalid_json_falls_back(self):
        """llm_client 返回非 dict：降级透传 cached_sql。"""
        llm_client = MagicMock()
        llm_client.invoke.return_value = "NOT A DICT"  # 错误形状
        node = make_value_rewrite_node(llm_client=llm_client)

        out = node({
            "user_query": "华北区的销售额",
            "cached_sql": "SELECT SUM(amt) FROM orders WHERE region='华东'",
            "cached_historical_query": "华东区的销售额"
        })

        self.assertEqual(out.get("adjusted_cached_sql"), "SELECT SUM(amt) FROM orders WHERE region='华东'")

    def test_llm_missing_adjusted_sql_falls_back(self):
        """llm_client 返回 dict 但无 adjusted_sql：降级透传 cached_sql。"""
        llm_client = MagicMock()
        llm_client.invoke.return_value = {
            "changed": True,
            "reason": "没带 sql"  # 缺少 adjusted_sql
        }
        node = make_value_rewrite_node(llm_client=llm_client)

        out = node({
            "user_query": "华北区的销售额",
            "cached_sql": "SELECT SUM(amt) FROM orders WHERE region='华东'",
            "cached_historical_query": "华东区的销售额"
        })

        self.assertEqual(out.get("adjusted_cached_sql"), "SELECT SUM(amt) FROM orders WHERE region='华东'")

    def test_trace_log_updated(self):
        """trace_log 被正确追加。"""
        llm_client = MagicMock()
        llm_client.invoke.return_value = {
            "adjusted_sql": "SELECT 42",
            "changed": True,
            "reason": "测试"
        }
        node = make_value_rewrite_node(llm_client=llm_client)

        out = node({
            "user_query": "q",
            "cached_sql": "SELECT 42",
            "cached_historical_query": "old q",
            "trace_log": ["prev log"]
        })

        self.assertEqual(out.get("trace_log", [])[0], "prev log")
        self.assertIn("ValueRewrite", out.get("trace_log", [])[1])


if __name__ == "__main__":
    unittest.main()

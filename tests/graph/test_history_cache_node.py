# ============================================================================
# HistoryCache 节点单元测试（change: cache-hit-ux-and-layout）
# ============================================================================
# 验证 make_history_cache_node 的 cache_check 事件 payload 在三种命中情况下
# matched_metric_name 字段的正确性：
#   - 命中 metric_definition：payload.matched_metric_name 为指标名
#   - 命中 session_history：payload.matched_metric_name 为 None
#   - 未命中：payload.matched_metric_name 为 None
#
# 运行: pytest tests/graph/test_history_cache_node.py -v
# ============================================================================

import unittest
from unittest.mock import MagicMock, patch

from src.graph.main_graph import make_history_cache_node
from src.memory.history_cache import CacheResult


class TestHistoryCacheNodeEmit(unittest.TestCase):
    """HistoryCache 节点 cache_check 事件 payload 测试"""

    def setUp(self):
        """测试前置：mock emit_safe / get_*_ctx，隔离事件发送与上下文依赖。"""
        self.emit_patcher = patch('src.graph.main_graph.emit_safe')
        self.mock_emit = self.emit_patcher.start()
        self.addCleanup(self.emit_patcher.stop)

        # get_session_memory_ctx 返回 None -> session_id="" -> 跳过 recall_session_history
        self.sm_patcher = patch('src.graph.main_graph.get_session_memory_ctx', return_value=None)
        self.sm_patcher.start()
        self.addCleanup(self.sm_patcher.stop)

        self.um_patcher = patch('src.graph.main_graph.get_user_memory_ctx', return_value=None)
        self.um_patcher.start()
        self.addCleanup(self.um_patcher.stop)

    def _make_state(self):
        return {
            "user_query": "查销售额",
            "user_id": "u1",
            "database_filter": "db1",
            "conversation_history": [],
            "metric_definitions": [
                {"name": "销售额", "description": "SUM", "sql_pattern": "SELECT SUM(amount) FROM sales"},
            ],
        }

    def _build_node(self, check_result: CacheResult):
        """构造节点：history_cache.check 返回预设 CacheResult"""
        history_cache = MagicMock()
        history_cache.check.return_value = check_result
        return make_history_cache_node(history_cache)

    def test_emit_metric_definition_hit_with_name(self):
        """命中 metric_definition：payload.matched_metric_name 为指标名"""
        node = self._build_node(CacheResult(
            hit=True,
            cached_sql="SELECT SUM(amount) FROM sales",
            source="metric_definition",
            confidence=0.92,
            matched_metric_name="销售额",
        ))
        node(self._make_state())

        self.mock_emit.assert_called_once()
        event_name, payload = self.mock_emit.call_args[0]
        self.assertEqual(event_name, "cache_check")
        self.assertTrue(payload["hit"])
        self.assertEqual(payload["source"], "metric_definition")
        self.assertEqual(payload["matched_metric_name"], "销售额")

    def test_emit_session_history_hit_without_metric_name(self):
        """命中 session_history：payload.matched_metric_name 为 None"""
        node = self._build_node(CacheResult(
            hit=True,
            cached_sql="SELECT SUM(amount) FROM sales WHERE product='Apple'",
            source="session_history",
            confidence=0.95,
            matched_metric_name=None,
        ))
        node(self._make_state())

        self.mock_emit.assert_called_once()
        event_name, payload = self.mock_emit.call_args[0]
        self.assertEqual(event_name, "cache_check")
        self.assertTrue(payload["hit"])
        self.assertEqual(payload["source"], "session_history")
        self.assertIsNone(payload["matched_metric_name"])

    def test_emit_miss_without_metric_name(self):
        """未命中：payload.matched_metric_name 为 None"""
        node = self._build_node(CacheResult(
            hit=False,
            cached_sql=None,
            source=None,
            confidence=0.0,
            matched_metric_name=None,
        ))
        node(self._make_state())

        self.mock_emit.assert_called_once()
        event_name, payload = self.mock_emit.call_args[0]
        self.assertEqual(event_name, "cache_check")
        self.assertFalse(payload["hit"])
        self.assertIsNone(payload["matched_metric_name"])


if __name__ == "__main__":
    unittest.main()

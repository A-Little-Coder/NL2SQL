# ============================================================================
# HistoryCache 节点单元测试（change: cache-hit-ux-and-layout + relax-session-write-gate）
# ============================================================================
# 验证 make_history_cache_node 的 cache_check 事件 payload 在三种命中情况下
# matched_metric_name 字段的正确性，以及 fallback 路径的 reuse_eligible 过滤。
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

    # ── fallback 过滤测试（relax-session-write-gate）────────────────────

    def test_fallback_filters_reuse_ineligible_turns(self):
        """fallback 路径过滤 reuse_eligible=False 的轮次，不传给 LLM 判断复用"""
        # 构造：get_session_memory_ctx=None → session_id="" → 跳过 recall → fallback 到 conversation_history
        state = self._make_state()
        state["conversation_history"] = [
            {"user_query": "查苹果", "final_sql": "SELECT SUM(amount) FROM sales", "reuse_eligible": True},
            {"user_query": "查香蕉", "final_sql": "SELECT invalid FROM bad", "reuse_eligible": False},
            {"user_query": "查橘子", "final_sql": "SELECT COUNT(*) FROM fruits", "reuse_eligible": True},
        ]
        # 不要 mock check 的返回值，保留真实调用，以便验证参数
        history_cache = MagicMock()
        history_cache.check.return_value = CacheResult(hit=False)
        node = make_history_cache_node(history_cache)
        node(state)

        # verify: check 被调用时 session_history 只包含 reuse_eligible=True 的轮次
        history_cache.check.assert_called_once()
        called_args = history_cache.check.call_args
        session_history = called_args[1].get("session_history", called_args[0][1] if len(called_args[0]) > 1 else [])
        self.assertEqual(len(session_history), 2)
        self.assertEqual(session_history[0]["user_query"], "查苹果")
        self.assertEqual(session_history[1]["user_query"], "查橘子")

    def test_fallback_old_data_compatibility(self):
        """旧数据缺 reuse_eligible 字段时按 bool(final_sql) 推导，旧文件行为不变"""
        state = self._make_state()
        state["conversation_history"] = [
            # 缺 reuse_eligible 字段 → 按 bool(final_sql) 推导
            {"user_query": "查苹果", "final_sql": "SELECT SUM(amount) FROM sales"},  # 有 final_sql → 视为 eligible
            {"user_query": "查香蕉", "final_sql": ""},  # 无 final_sql → 视为不可复用
        ]
        history_cache = MagicMock()
        history_cache.check.return_value = CacheResult(hit=False)
        node = make_history_cache_node(history_cache)
        node(state)

        history_cache.check.assert_called_once()
        called_args = history_cache.check.call_args
        session_history = called_args[1].get("session_history", called_args[0][1] if len(called_args[0]) > 1 else [])
        self.assertEqual(len(session_history), 1)
        self.assertEqual(session_history[0]["user_query"], "查苹果")

    def test_fallback_with_recalled_refs_uses_recall_not_conversation(self):
        """recall 有结果时不 fallback 到 conversation_history，reuse_eligible 过滤不生效"""
        state = self._make_state()
        state["conversation_history"] = [
            {"user_query": "查苹果", "final_sql": "SELECT SUM(amount) FROM sales", "reuse_eligible": False},
        ]
        # 设置 session_memory 返回 session_id 使 recall 触发
        mock_session = MagicMock()
        mock_session.session_id = "s1"
        sm_patcher = patch('src.graph.main_graph.get_session_memory_ctx', return_value=mock_session)
        sm_patcher.start()
        self.addCleanup(sm_patcher.stop)

        history_cache = MagicMock()
        # recall_session_history 返回非空结果
        from src.memory.session_recall import HistoricalSQLReference
        ref = HistoricalSQLReference(
            historical_query="查苹果",
            historical_sql="SELECT SUM(amount) FROM sales",
            rrf_score=0.8,
            dense_rank=1,
            bm25_rank=2,
            conversation_id="s1",
            turn_id=1,
        )
        history_cache.recall_session_history.return_value = [ref]
        history_cache.check.return_value = CacheResult(hit=False)
        node = make_history_cache_node(history_cache)
        node(state)

        history_cache.check.assert_called_once()
        called_args = history_cache.check.call_args
        session_history = called_args[1].get("session_history", called_args[0][1] if len(called_args[0]) > 1 else [])
        # 来自 recall，不是 conversation_history
        self.assertEqual(len(session_history), 1)
        self.assertEqual(session_history[0]["user_query"], "查苹果")


if __name__ == "__main__":
    unittest.main()

# ============================================================================
# SchemaFinalize 节点单元测试（D10：空表显式拒答）
# ============================================================================
# 运行: pytest tests/graph/test_schema_finalize_node.py -v
# ============================================================================

import unittest
from unittest.mock import patch, MagicMock

from src.graph.main_graph import make_schema_finalize_node


class TestSchemaFinalizeEmptyReject(unittest.TestCase):
    """schema_finalize 空表显式拒答（D10）"""

    @patch("src.graph.main_graph.emit_safe")
    def test_empty_schema_emits_schema_empty_and_sets_reason(self, mock_emit):
        """selected_schema=[] 时设 rejection_reason + emit schema_empty，不再静默 END"""
        node = make_schema_finalize_node(retriever=MagicMock(), data_dir=None)
        result = node({"selected_schema": [], "query_id": "qid1", "trace_log": []})

        # 设 rejection_reason（友好提示）
        self.assertIn("rejection_reason", result)
        self.assertIn("找到", result["rejection_reason"])
        # selected_schema 保持空、join_paths_text 空
        self.assertEqual(result["selected_schema"], [])
        self.assertEqual(result["join_paths_text"], "")
        # emit schema_empty 事件（带 reason）
        schema_empty_calls = [
            c for c in mock_emit.call_args_list
            if c.args and c.args[0] == "schema_empty"
        ]
        self.assertEqual(len(schema_empty_calls), 1)
        self.assertIn("reason", schema_empty_calls[0].args[1])
        self.assertIn("找到", schema_empty_calls[0].args[1]["reason"])

    @patch("src.preprocessing.schema_graph_builder.enrich_schema_with_join_paths")
    @patch("src.graph.main_graph.emit_safe")
    def test_nonempty_schema_does_not_emit_schema_empty(self, mock_emit, mock_enrich):
        """selected_schema 非空时不 emit schema_empty、不设 rejection_reason（正常放行）"""
        fake_table = {"table": "t"}
        mock_enrich.return_value = ([fake_table], "JOIN t1 ON ...")
        node = make_schema_finalize_node(retriever=MagicMock(), data_dir=None)
        result = node({"selected_schema": [fake_table], "query_id": "qid2", "trace_log": []})

        schema_empty_calls = [
            c for c in mock_emit.call_args_list
            if c.args and c.args[0] == "schema_empty"
        ]
        self.assertEqual(len(schema_empty_calls), 0)
        self.assertNotIn("rejection_reason", result)


if __name__ == "__main__":
    unittest.main()

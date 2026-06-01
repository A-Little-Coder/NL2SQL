# ============================================================================
# SchemaSelector 测试用例
# ============================================================================


import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.schema_selection.schema_selector import (
    SchemaSelector, MSchemaTable, MSchemaColumn, MSchemaFormat,
)
from src.retrieval.information_retrieval import RetrievedContext, RetrievedItem


class TestMSchemaFormat(unittest.TestCase):
    """M-schema 格式工具测试"""

    def test_create_mschema_schema(self):
        tables = [
            MSchemaTable(
                name="users",
                columns=[
                    MSchemaColumn(name="id", data_type="INTEGER", is_primary_key=True),
                    MSchemaColumn(name="name", data_type="TEXT", description="姓名",
                                  sample_values=["Alice", "Bob"]),
                ],
                description="用户表",
                row_count=100,
            )
        ]
        result = MSchemaFormat.create_mschema_schema(tables)

        self.assertEqual(result["version"], "1.0")
        self.assertEqual(len(result["tables"]), 1)
        self.assertEqual(result["tables"][0]["name"], "users")
        self.assertEqual(len(result["tables"][0]["columns"]), 2)
        self.assertTrue(result["tables"][0]["columns"][0]["is_primary_key"])

    def test_format_for_llm(self):
        tables = [
            MSchemaTable(
                name="orders",
                columns=[
                    MSchemaColumn(name="id", data_type="INTEGER", is_primary_key=True),
                    MSchemaColumn(name="user_id", data_type="INTEGER", is_foreign_key=True,
                                  references="users.id"),
                ],
            )
        ]
        mschema_dict = MSchemaFormat.create_mschema_schema(tables)
        text = MSchemaFormat.format_for_llm(mschema_dict)

        self.assertIn("orders", text)
        self.assertIn("PK", text)
        self.assertIn("FK", text)
        self.assertIn("users.id", text)


class TestToMschema(unittest.TestCase):
    """to_mschema 转换测试"""

    def test_basic_conversion(self):
        ctx = RetrievedContext(
            tables=[RetrievedItem(item_type="table", name="orders")],
            columns=[
                RetrievedItem(item_type="column", name="id", table_name="orders",
                              metadata={"data_type": "INTEGER", "is_primary_key": True}),
                RetrievedItem(item_type="column", name="amount", table_name="orders",
                              metadata={"data_type": "REAL"}),
            ],
        )
        selector = SchemaSelector()
        result = selector.to_mschema(ctx)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, "orders")
        self.assertEqual(len(result[0].columns), 2)

    def test_column_without_table_in_tables_list(self):
        """列所属表不在 tables 列表中时也能正确处理"""
        ctx = RetrievedContext(
            tables=[],
            columns=[
                RetrievedItem(item_type="column", name="x", table_name="products",
                              metadata={"data_type": "TEXT"}),
            ],
        )
        selector = SchemaSelector()
        result = selector.to_mschema(ctx)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, "products")


class TestEvaluateColumnRelevance(unittest.TestCase):
    """列相关性评估测试"""

    def test_without_llm(self):
        """无 LLM 时所有列默认 1.0"""
        selector = SchemaSelector()
        tables = [MSchemaTable(name="t", columns=[MSchemaColumn(name="c", data_type="TEXT")])]
        result = selector.evaluate_column_relevance(tables, "test")
        self.assertEqual(result[0].columns[0].relevance_score, 1.0)

    def test_with_llm_mock(self):
        mock_llm = MagicMock()
        mock_llm.chat_json.return_value = {
            "scores": [
                {"table": "orders", "column": "amount", "score": 0.9, "reason": "直接相关"},
                {"table": "orders", "column": "id", "score": 0.3, "reason": "弱相关"},
            ]
        }
        selector = SchemaSelector(llm_client=mock_llm)
        tables = [
            MSchemaTable(name="orders", columns=[
                MSchemaColumn(name="id", data_type="INTEGER"),
                MSchemaColumn(name="amount", data_type="REAL"),
            ])
        ]
        result = selector.evaluate_column_relevance(tables, "查询总销售额")

        col_amount = next(c for c in result[0].columns if c.name == "amount")
        col_id = next(c for c in result[0].columns if c.name == "id")
        self.assertAlmostEqual(col_amount.relevance_score, 0.9)
        self.assertAlmostEqual(col_id.relevance_score, 0.3)


class TestFilterColumns(unittest.TestCase):
    """列过滤测试"""

    def test_filter_by_threshold(self):
        selector = SchemaSelector(relevance_threshold=0.5)
        tables = [
            MSchemaTable(name="t", columns=[
                MSchemaColumn(name="a", data_type="TEXT", relevance_score=0.8),
                MSchemaColumn(name="b", data_type="TEXT", relevance_score=0.3),
                MSchemaColumn(name="c", data_type="TEXT", relevance_score=0.7),
            ])
        ]
        result = selector.filter_columns(tables)
        col_names = [c.name for c in result[0].columns]
        self.assertIn("a", col_names)
        self.assertIn("c", col_names)
        self.assertNotIn("b", col_names)

    def test_keep_primary_key_below_threshold(self):
        """主键即使低于阈值也保留"""
        selector = SchemaSelector(relevance_threshold=0.5)
        tables = [
            MSchemaTable(name="t", columns=[
                MSchemaColumn(name="id", data_type="INTEGER",
                              relevance_score=0.1, is_primary_key=True),
                MSchemaColumn(name="x", data_type="TEXT", relevance_score=0.9),
            ])
        ]
        result = selector.filter_columns(tables)
        col_names = [c.name for c in result[0].columns]
        self.assertIn("id", col_names)

    def test_keep_foreign_key_below_threshold(self):
        """外键即使低于阈值也保留"""
        selector = SchemaSelector(relevance_threshold=0.5)
        tables = [
            MSchemaTable(name="t", columns=[
                MSchemaColumn(name="user_id", data_type="INTEGER",
                              relevance_score=0.1, is_foreign_key=True),
            ])
        ]
        result = selector.filter_columns(tables)
        self.assertEqual(len(result[0].columns), 1)

    def test_drop_table_with_no_columns(self):
        """没有列的表会被丢弃"""
        selector = SchemaSelector(relevance_threshold=0.5)
        tables = [
            MSchemaTable(name="t", columns=[
                MSchemaColumn(name="x", data_type="TEXT", relevance_score=0.1),
            ])
        ]
        result = selector.filter_columns(tables)
        self.assertEqual(len(result), 0)


class TestSelectFlow(unittest.TestCase):
    """完整 select 流程测试"""

    def test_full_flow(self):
        mock_llm = MagicMock()
        mock_llm.chat_json.return_value = {
            "scores": [
                {"table": "orders", "column": "amount", "score": 0.9},
                {"table": "orders", "column": "date", "score": 0.6},
                {"table": "orders", "column": "note", "score": 0.2},
            ]
        }
        selector = SchemaSelector(llm_client=mock_llm, relevance_threshold=0.5)

        ctx = RetrievedContext(
            tables=[RetrievedItem(item_type="table", name="orders")],
            columns=[
                RetrievedItem(item_type="column", name="amount", table_name="orders",
                              metadata={"data_type": "REAL"}),
                RetrievedItem(item_type="column", name="date", table_name="orders",
                              metadata={"data_type": "DATE"}),
                RetrievedItem(item_type="column", name="note", table_name="orders",
                              metadata={"data_type": "TEXT"}),
            ],
        )
        result = selector.select(ctx, "查询本月销售额")

        self.assertEqual(len(result), 1)
        col_names = [c.name for c in result[0].columns]
        self.assertIn("amount", col_names)
        self.assertIn("date", col_names)
        self.assertNotIn("note", col_names)


if __name__ == "__main__":
    unittest.main()

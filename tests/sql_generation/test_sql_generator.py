# ============================================================================
# SQLGenerator 测试用例
# ============================================================================


import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.sql_generation.sql_generator import SQLGenerator, SQLCandidate, SQLStatus
from src.schema_selection.schema_selector import MSchemaTable, MSchemaColumn


class TestExtractEntities(unittest.TestCase):
    """实体识别测试"""

    def setUp(self):
        self.gen = SQLGenerator()

    def test_date_entity(self):
        result = self.gen.extract_entities("显示2023年的销售额")
        self.assertIn("DATE", result)
        self.assertIn("2023年", result["DATE"])

    def test_relative_date(self):
        result = self.gen.extract_entities("去年销售额")
        self.assertIn("DATE", result)

    def test_money_entity(self):
        result = self.gen.extract_entities("超过100万元的订单")
        self.assertIn("MONEY", result)

    def test_number_entity(self):
        result = self.gen.extract_entities("销售额增长50%")
        self.assertIn("NUMBER", result)


class TestMaskQuery(unittest.TestCase):
    """查询掩码测试"""

    def setUp(self):
        self.gen = SQLGenerator()

    def test_mask_date(self):
        entities = {"DATE": ["2023年"]}
        result = self.gen.mask_query("显示2023年的销售额", entities)
        self.assertIn("[DATE]", result)
        self.assertNotIn("2023年", result)

    def test_mask_multiple(self):
        entities = {"DATE": ["去年"], "MONEY": ["100万元"]}
        result = self.gen.mask_query("去年销售额超过100万元的", entities)
        self.assertIn("[DATE]", result)
        self.assertIn("[MONEY]", result)

    def test_no_entities(self):
        result = self.gen.mask_query("全部订单", {})
        self.assertEqual(result, "全部订单")


class TestSelectFewShot(unittest.TestCase):
    """Few-shot 选择测试"""

    def test_basic_selection(self):
        gen = SQLGenerator()
        result = gen.select_few_shot_examples("显示[LOCATION]的销售额", [])
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

    def test_multi_table_selection(self):
        gen = SQLGenerator()
        result = gen.select_few_shot_examples("查询多表", [], is_multi_table=True)
        for ex in result:
            self.assertTrue(ex["is_multi_table"])


class TestGenerate(unittest.TestCase):
    """SQL 生成流程测试"""

    def test_no_llm_returns_empty(self):
        gen = SQLGenerator()
        result = gen.generate([], "test")
        self.assertEqual(result, [])

    def test_generate_with_mock_llm(self):
        mock_llm = MagicMock()
        mock_llm.stream.return_value = iter([(__import__("json").dumps({
            "candidates": [
                {"sql": "SELECT SUM(amount) FROM orders WHERE region = '北京'", "reason": "聚合查询"},
                {"sql": "SELECT region, SUM(amount) FROM orders GROUP BY region HAVING region = '北京'", "reason": "分组聚合"},
            ]
        }, ensure_ascii=False), None)])
        gen = SQLGenerator(llm_client=mock_llm, num_candidates=5)

        schema = [
            MSchemaTable(name="orders", columns=[
                MSchemaColumn(name="amount", data_type="REAL"),
                MSchemaColumn(name="region", data_type="TEXT"),
            ])
        ]

        result = gen.generate(schema, "查询北京的总销售额")

        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)
        for c in result:
            self.assertIsInstance(c, SQLCandidate)
            self.assertEqual(c.status, SQLStatus.VALIDATED)
            self.assertIsNotNone(c.sql)

    def test_generate_filters_dangerous_sql(self):
        """危险 SQL 应该被过滤"""
        mock_llm = MagicMock()
        mock_llm.stream.return_value = iter([(__import__("json").dumps({
            "candidates": [
                {"sql": "SELECT * FROM orders", "reason": "安全"},
                {"sql": "DROP TABLE orders", "reason": "危险"},
            ]
        }, ensure_ascii=False), None)])
        gen = SQLGenerator(llm_client=mock_llm)
        result = gen.generate([], "test")
        # DROP 应该被过滤
        for c in result:
            self.assertNotIn("DROP", c.sql.upper())


class TestSQLCandidate(unittest.TestCase):
    """SQLCandidate 数据类测试"""

    def test_default_status(self):
        c = SQLCandidate(id="abc", sql="SELECT 1")
        self.assertEqual(c.status, SQLStatus.PENDING)

    def test_custom_status(self):
        c = SQLCandidate(id="abc", sql="SELECT 1", status=SQLStatus.VALIDATED)
        self.assertEqual(c.status, SQLStatus.VALIDATED)


if __name__ == "__main__":
    unittest.main()

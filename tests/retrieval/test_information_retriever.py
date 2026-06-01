# ============================================================================
# InformationRetrieval 测试用例
# ============================================================================
# 运行方法:
#   python -m unittest tests.retrieval.test_information_retriever -v
# ============================================================================


import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.retrieval.information_retrieval import (
    InformationRetrieval, RetrievedItem, RetrievedContext,
)


class TestSimpleKeywordExtract(unittest.TestCase):
    """测试简单关键词提取回退方案"""

    def test_removes_stopwords(self):
        result = InformationRetrieval._simple_keyword_extract("显示查询")
        for w in result:
            self.assertNotIn(w, {"显示", "查询"})

    def test_empty_input(self):
        result = InformationRetrieval._simple_keyword_extract("")
        self.assertEqual(result, [])

    def test_english_keywords(self):
        result = InformationRetrieval._simple_keyword_extract("find Apple revenue")
        self.assertIn("Apple", result)
        self.assertIn("revenue", result)

    def test_chinese_query_returns_keywords(self):
        result = InformationRetrieval._simple_keyword_extract("苹果公司的营收")
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)


class TestLLMKeywordExtract(unittest.TestCase):
    """测试 LLM 关键词提取（Mock）"""

    def test_llm_extract_success(self):
        mock_client = MagicMock()
        mock_client.chat_json.return_value = {
            "keywords": ["去年", "北京", "销售额"]
        }
        ir = InformationRetrieval(llm_client=mock_client)
        result = ir.extract_keywords("显示去年北京地区的销售额")
        # LLM 返回什么就是什么
        self.assertEqual(result, ["去年", "北京", "销售额"])

    def test_llm_extract_fallback(self):
        mock_client = MagicMock()
        mock_client.chat_json.side_effect = Exception("API Error")
        ir = InformationRetrieval(llm_client=mock_client)
        result = ir.extract_keywords("苹果公司的营收")
        # 回退到简单提取，至少有结果
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)


class TestRetrieveValues(unittest.TestCase):
    """测试 LSH 值检索（Mock）"""

    def test_no_lsh_indexer(self):
        ir = InformationRetrieval()
        result = ir.retrieve_values(["苹果"])
        self.assertEqual(result, [])

    def test_lsh_retrieve_with_mock(self):
        ir = InformationRetrieval(lsh_threshold=0.3)

        mock_lsh_indexer = MagicMock()
        mock_lsh_indexer._loaded_lsh = MagicMock()
        mock_lsh_indexer._loaded_minhashes = MagicMock()
        mock_lsh_indexer.query.return_value = {
            "products": {"name": ["苹果", "苹果汁"]}
        }
        ir.lsh_indexer = mock_lsh_indexer

        # Patch 模块级 LSHIndexer
        with patch('src.retrieval.information_retrieval.LSHIndexer') as MockLSH:
            mock_mh = MagicMock()
            MockLSH.create_minhash.return_value = mock_mh
            MockLSH.jaccard_similarity.return_value = 0.7

            result = ir.retrieve_values(["苹果"], top_k=5)

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].item_type, "value")
        self.assertEqual(result[0].score, 0.7)

    def test_lsh_below_threshold_filtered(self):
        ir = InformationRetrieval(lsh_threshold=0.8)

        mock_lsh_indexer = MagicMock()
        mock_lsh_indexer._loaded_lsh = MagicMock()
        mock_lsh_indexer._loaded_minhashes = MagicMock()
        mock_lsh_indexer.query.return_value = {
            "products": {"name": ["苹果"]}
        }
        ir.lsh_indexer = mock_lsh_indexer

        with patch('src.retrieval.information_retrieval.LSHIndexer') as MockLSH:
            mock_mh = MagicMock()
            MockLSH.create_minhash.return_value = mock_mh
            MockLSH.jaccard_similarity.return_value = 0.3  # 低于阈值

            result = ir.retrieve_values(["苹果"])

        # 应该被过滤掉
        self.assertEqual(len(result), 0)


class TestRetrieveSchema(unittest.TestCase):
    """测试语义 schema 检索（Mock）"""

    def test_no_vector_store(self):
        ir = InformationRetrieval()
        result = ir.retrieve_schema("查询销售额")
        self.assertEqual(result, {"tables": [], "columns": []})

    def test_schema_retrieve_with_mock(self):
        mock_vs = MagicMock()
        mock_vs.query.return_value = [
            {
                "id": "orders_amount",
                "metadata": {"table_name": "orders", "column_name": "amount", "item_type": "column"},
                "document": "orders 表中的 amount 字段",
                "distance": 0.2,
            },
            {
                "id": "orders_table",
                "metadata": {"table_name": "orders", "item_type": "table"},
                "document": "orders 表",
                "distance": 0.3,
            },
        ]

        mock_vectorizer = MagicMock()
        mock_vectorizer.embed_texts.return_value = {"dense": [[0.1] * 1024]}

        ir = InformationRetrieval(vector_store=mock_vs)
        ir._vectorizer = mock_vectorizer

        result = ir.retrieve_schema("查询销售额")

        self.assertEqual(len(result["tables"]), 1)
        self.assertEqual(len(result["columns"]), 1)
        self.assertEqual(result["tables"][0].name, "orders")
        self.assertAlmostEqual(result["columns"][0].score, 0.8)

    def test_schema_dedup(self):
        """测试表/列去重"""
        mock_vs = MagicMock()
        mock_vs.query.return_value = [
            {
                "id": "orders_amount_1",
                "metadata": {"table_name": "orders", "column_name": "amount", "item_type": "column"},
                "document": "", "distance": 0.2,
            },
            {
                "id": "orders_amount_2",
                "metadata": {"table_name": "orders", "column_name": "amount", "item_type": "column"},
                "document": "", "distance": 0.25,
            },
        ]
        mock_vectorizer = MagicMock()
        mock_vectorizer.embed_texts.return_value = {"dense": [[0.1] * 1024]}

        ir = InformationRetrieval(vector_store=mock_vs)
        ir._vectorizer = mock_vectorizer

        result = ir.retrieve_schema("查询销售额")
        # 同一个列只出现一次
        self.assertEqual(len(result["columns"]), 1)


class TestRetrieveFull(unittest.TestCase):
    """测试完整的 retrieve 流程"""

    def test_retrieve_with_mocks(self):
        mock_llm = MagicMock()
        mock_llm.chat_json.return_value = {"keywords": ["苹果", "销售额"]}

        mock_lsh = MagicMock()
        mock_lsh._loaded_lsh = MagicMock()
        mock_lsh._loaded_minhashes = MagicMock()
        mock_lsh.query.return_value = {"products": {"name": ["苹果"]}}

        mock_vs = MagicMock()
        mock_vs.query.return_value = [
            {
                "id": "products_name",
                "metadata": {"table_name": "products", "column_name": "name", "item_type": "column"},
                "document": "products.name",
                "distance": 0.15,
            },
        ]

        mock_vectorizer = MagicMock()
        mock_vectorizer.embed_texts.return_value = {"dense": [[0.1] * 1024]}

        ir = InformationRetrieval(
            llm_client=mock_llm,
            lsh_indexer=mock_lsh,
            vector_store=mock_vs,
            lsh_threshold=0.3,
        )
        ir._vectorizer = mock_vectorizer

        with patch('src.retrieval.information_retrieval.LSHIndexer') as MockLSH:
            mock_mh = MagicMock()
            MockLSH.create_minhash.return_value = mock_mh
            MockLSH.jaccard_similarity.return_value = 0.7

            context = ir.retrieve("查一下苹果的销售额")

        self.assertIsInstance(context, RetrievedContext)
        self.assertEqual(context.keywords, ["苹果", "销售额"])
        self.assertGreaterEqual(context.lsh_hit_count, 0)
        self.assertIsInstance(context.vector_top_scores, list)


class TestRetrievedContext(unittest.TestCase):
    """测试 RetrievedContext 辅助方法"""

    def test_get_all_table_names(self):
        ctx = RetrievedContext(
            tables=[RetrievedItem(item_type="table", name="orders")],
            columns=[RetrievedItem(item_type="column", name="id", table_name="orders"),
                     RetrievedItem(item_type="column", name="name", table_name="users")],
            values=[RetrievedItem(item_type="value", name="苹果", table_name="products")],
        )
        tables = ctx.get_all_table_names()
        self.assertIn("orders", tables)
        self.assertIn("users", tables)
        self.assertIn("products", tables)

    def test_empty_context(self):
        ctx = RetrievedContext()
        self.assertEqual(ctx.tables, [])
        self.assertEqual(ctx.lsh_hit_count, 0)
        self.assertEqual(ctx.vector_top_scores, [])


class TestEnhanceWithSchema(unittest.TestCase):
    """测试根据值补充 schema 信息"""

    def test_enhance_adds_missing_table_and_column(self):
        ir = InformationRetrieval()
        ctx = RetrievedContext(
            tables=[],
            columns=[],
            values=[
                RetrievedItem(
                    item_type="value", name="苹果",
                    table_name="products", score=0.8,
                    metadata={"column_name": "name"},
                ),
            ],
        )
        result = ir.enhance_with_schema(ctx)
        self.assertTrue(any(t.name == "products" for t in result.tables))
        self.assertTrue(any(c.name == "name" and c.table_name == "products" for c in result.columns))

    def test_enhance_no_duplicate(self):
        """已有表/列不应该重复添加"""
        ir = InformationRetrieval()
        ctx = RetrievedContext(
            tables=[RetrievedItem(item_type="table", name="products")],
            columns=[RetrievedItem(item_type="column", name="name", table_name="products")],
            values=[
                RetrievedItem(
                    item_type="value", name="苹果",
                    table_name="products", score=0.8,
                    metadata={"column_name": "name"},
                ),
            ],
        )
        result = ir.enhance_with_schema(ctx)
        self.assertEqual(len([t for t in result.tables if t.name == "products"]), 1)
        self.assertEqual(len([c for c in result.columns if c.name == "name" and c.table_name == "products"]), 1)


if __name__ == "__main__":
    unittest.main()

# ============================================================================
# VectorStoreManager 测试用例
# ============================================================================
# 使用临时目录的 ChromaDB 进行测试，不依赖持久化数据
#
# 运行方法:
#   python -m unittest tests.preprocessing.test_vector_store -v
# ============================================================================


import os
import sys
import shutil
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.preprocessing.vector_store import VectorStoreManager


class TestVectorStoreManager(unittest.TestCase):
    """向量存储管理器单元测试"""

    tmp_dir = None
    manager = None

    @classmethod
    def setUpClass(cls):
        cls.tmp_dir = tempfile.mkdtemp(prefix="nl2sql_test_vs_")
        cls.manager = VectorStoreManager(
            collection_name="test_schemas",
            persist_directory=cls.tmp_dir,
        )

    @classmethod
    def tearDownClass(cls):
        if cls.tmp_dir and os.path.exists(cls.tmp_dir):
            shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    def test_01_init(self):
        """测试初始化"""
        self.assertIsNotNone(self.manager.client)
        self.assertIsNotNone(self.manager.collection)
        self.assertEqual(self.manager.collection.count(), 0)

    def test_02_add_embeddings(self):
        """测试添加 embedding"""
        embeddings = [
            {
                "id": "orders_id",
                "embedding": [0.1] * 1024,
                "metadata": {"table_name": "orders", "column_name": "id", "database": "test_db"},
                "document": "orders 表中的 id 字段",
            },
            {
                "id": "orders_amount",
                "embedding": [0.2] * 1024,
                "metadata": {"table_name": "orders", "column_name": "amount", "database": "test_db"},
                "document": "orders 表中的 amount 字段",
            },
            {
                "id": "users_name",
                "embedding": [0.3] * 1024,
                "metadata": {"table_name": "users", "column_name": "name", "database": "test_db"},
                "document": "users 表中的 name 字段",
            },
        ]
        result = self.manager.add_embeddings(embeddings)
        self.assertTrue(result)
        self.assertEqual(self.manager.collection.count(), 3)

    def test_03_add_embeddings_upsert(self):
        """测试重复 ID 更新"""
        embeddings = [
            {
                "id": "orders_id",
                "embedding": [0.5] * 1024,
                "metadata": {"table_name": "orders", "column_name": "id", "database": "test_db"},
                "document": "更新后的描述",
            },
        ]
        self.manager.add_embeddings(embeddings)
        self.assertEqual(self.manager.collection.count(), 3)  # 数量不变

    def test_04_query(self):
        """测试向量查询"""
        results = self.manager.query(
            query_embedding=[0.15] * 1024,
            n_results=2,
        )
        self.assertIsInstance(results, list)
        self.assertGreater(len(results), 0)
        self.assertIn("id", results[0])
        self.assertIn("distance", results[0])

    def test_05_query_with_filter(self):
        """测试带过滤条件的查询"""
        results = self.manager.query(
            query_embedding=[0.15] * 1024,
            n_results=10,
            where_filter={"table_name": "orders"},
        )
        for r in results:
            self.assertEqual(r["metadata"]["table_name"], "orders")

    def test_06_get_all_tables(self):
        """测试获取所有表名"""
        tables = self.manager.get_all_tables()
        self.assertIn("orders", tables)
        self.assertIn("users", tables)

    def test_07_get_all_tables_with_filter(self):
        """测试按数据库过滤表名"""
        tables = self.manager.get_all_tables(database_name="test_db")
        self.assertIn("orders", tables)
        # 不存在的数据库
        tables_empty = self.manager.get_all_tables(database_name="nonexist")
        self.assertEqual(tables_empty, [])

    def test_08_get_stats(self):
        """测试统计信息"""
        stats = self.manager.get_stats()
        self.assertEqual(stats["total_embeddings"], 3)
        self.assertEqual(stats["unique_tables"], 2)
        self.assertIn("test_db", stats["databases"])

    def test_09_delete_by_database(self):
        """测试按数据库删除"""
        result = self.manager.delete_by_database("test_db")
        self.assertTrue(result)
        self.assertEqual(self.manager.collection.count(), 0)

    def test_10_clear(self):
        """测试清空集合"""
        # 先添加一些数据
        self.manager.add_embeddings([
            {"id": "tmp_1", "embedding": [0.1] * 1024,
             "metadata": {"table_name": "t1"}, "document": "doc"},
        ])
        self.manager.clear()
        self.assertEqual(self.manager.collection.count(), 0)

    def test_11_add_empty_list(self):
        """测试添加空列表"""
        result = self.manager.add_embeddings([])
        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()

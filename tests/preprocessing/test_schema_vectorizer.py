# ============================================================================
# SchemaVectorizer 测试用例
# ============================================================================
# 测试策略:
#   - 单元测试: 用 Mock 模型验证 embed_texts / embed_schema 流程
#   - 静态方法: format_column_description 直接测试
#   - 集成测试（可选, 标注 @skip）: 真实加载 BGE-M3
#
# 运行方法:
#   pytest tests/preprocessing/test_schema_vectorizer.py -v
# ============================================================================


import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.preprocessing.schema_vectorizer import SchemaVectorizer


class TestFormatColumnDescription(unittest.TestCase):
    """静态方法 format_column_description 的测试"""

    def test_with_description_and_table(self):
        col = {"name": "raceId", "type": "INTEGER",
               "description": "the unique identification number identifying the race"}
        out = SchemaVectorizer.format_column_description(col, "races")
        self.assertIn("races", out)
        self.assertIn("unique identification", out)

    def test_with_description_no_table(self):
        col = {"name": "x", "description": "abc"}
        out = SchemaVectorizer.format_column_description(col)
        self.assertEqual(out, "abc")

    def test_without_description(self):
        col = {"name": "id", "type": "INTEGER"}
        out = SchemaVectorizer.format_column_description(col, "users")
        self.assertIn("users", out)
        self.assertIn("id", out)
        self.assertIn("INTEGER", out)

    def test_without_anything(self):
        col = {}
        out = SchemaVectorizer.format_column_description(col)
        # 默认类型 TEXT，字段名为空
        self.assertIn("TEXT", out)


class TestEmbedTextsMocked(unittest.TestCase):
    """用 Mock 模型验证 embed_texts 的字典组装逻辑"""

    def setUp(self):
        self.vec = SchemaVectorizer()
        # 构造一个 Mock 模型，返回固定形状的 dense 向量
        mock_model = MagicMock()
        mock_model.encode.return_value = {
            "dense_vecs": np.array([[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]),
        }
        self.vec.model = mock_model

    def test_embed_texts_returns_dense(self):
        out = self.vec.embed_texts(["a", "b"], return_dense=True)
        self.assertIn("dense", out)
        self.assertEqual(len(out["dense"]), 2)
        self.assertEqual(len(out["dense"][0]), 3)

    def test_embed_texts_empty_input(self):
        out = self.vec.embed_texts([])
        self.assertEqual(out["dense"], [])

    def test_embed_texts_without_load_raises(self):
        v = SchemaVectorizer()
        v.model = None
        with self.assertRaises(RuntimeError):
            v.embed_texts(["x"])


class TestEmbedSchemaMocked(unittest.TestCase):
    """embed_schema 端到端流程的 Mock 测试"""

    def setUp(self):
        self.vec = SchemaVectorizer()
        # mock: 返回 N 行 dense 向量，N 由调用时 texts 长度决定
        def mock_encode(texts, **kwargs):
            return {"dense_vecs": np.array([[float(i)] * 4 for i in range(len(texts))])}
        mock_model = MagicMock()
        mock_model.encode.side_effect = mock_encode
        self.vec.model = mock_model

    def test_embed_schema_basic(self):
        schema_info = {
            "table_name": "users",
            "columns": [
                {"name": "id", "type": "INTEGER"},
                {"name": "name", "type": "TEXT", "description": "用户姓名"},
            ],
        }
        out = self.vec.embed_schema(schema_info)
        self.assertEqual(out["table_name"], "users")
        self.assertIsNotNone(out["table_embedding"])
        self.assertEqual(len(out["columns"]), 2)
        for col in out["columns"]:
            self.assertIn("embedding", col)
            self.assertIsNotNone(col["embedding"])


class TestGetEmbeddingDimension(unittest.TestCase):
    def test_default_dimension(self):
        vec = SchemaVectorizer()
        # 未加载模型时返回默认值
        self.assertEqual(vec.get_embedding_dimension(), 1024)


def run_tests():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [TestFormatColumnDescription, TestEmbedTextsMocked,
                TestEmbedSchemaMocked, TestGetEmbeddingDimension]:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)

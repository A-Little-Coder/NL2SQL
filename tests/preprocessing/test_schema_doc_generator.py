# ============================================================================
# 测试：Schema 列文档生成器
# ============================================================================


import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from src.preprocessing.schema_doc_generator import SchemaColumnDocGenerator


class TestSchemaColumnDocGenerator:
    """测试 Schema 列文档生成器"""

    def test_format_column_document_basic(self):
        """基础文档格式化"""
        doc = SchemaColumnDocGenerator.format_column_document(
            table_name="users",
            original_column_name="id",
            column_name="用户ID",
            data_type="INTEGER",
        )
        assert "users" in doc
        assert "id" in doc
        assert "用户ID" in doc
        assert doc.count("用户ID") == 2  # 末尾 boost

    def test_format_column_document_with_description(self):
        """带描述的文档"""
        doc = SchemaColumnDocGenerator.format_column_document(
            table_name="orders",
            original_column_name="total_amt",
            column_name="订单总额",
            data_type="DECIMAL",
            column_description="订单总金额，包含运费",
        )
        assert "订单总额" in doc
        assert "订单总金额，包含运费" in doc

    def test_format_column_document_boost(self):
        """验证末尾 boost"""
        doc = SchemaColumnDocGenerator.format_column_document(
            table_name="t",
            original_column_name="x",
            column_name="销量",
        )
        # 结构：t x 销量 销量
        assert doc.startswith("t x 销量")
        assert doc.endswith("销量")

    def test_build_column_metadata_basic(self):
        """基础 metadata"""
        meta = SchemaColumnDocGenerator.build_column_metadata(
            database="testdb",
            table_name="users",
            original_column_name="id",
            column_name="用户ID",
            data_type="INTEGER",
            is_primary_key=True,
        )
        assert meta["database"] == "testdb"
        assert meta["table_name"] == "users"
        assert meta["original_column_name"] == "id"
        assert meta["is_primary_key"] is True
        assert meta["is_foreign_key"] is False

    def test_build_column_metadata_with_samples(self):
        """sample_values 用 | 连接"""
        meta = SchemaColumnDocGenerator.build_column_metadata(
            database="testdb",
            table_name="t",
            original_column_name="col",
            column_name="col",
            sample_values=["a", "b", "c"],
        )
        assert meta["sample_values"] == "a|b|c"

    def test_build_column_metadata_foreign_key(self):
        """外键 metadata"""
        meta = SchemaColumnDocGenerator.build_column_metadata(
            database="testdb",
            table_name="orders",
            original_column_name="user_id",
            column_name="用户ID",
            is_foreign_key=True,
            references="users.id",
        )
        assert meta["is_foreign_key"] is True
        assert meta["references"] == "users.id"

    def test_build_doc_from_connector_schema(self):
        """从 connector 风格 schema 构建"""
        col_schema = {
            "name": "id",
            "type": "INTEGER",
            "primary_key": True,
            "description": "用户主键",
            "sample_values": [1, 2, 3],
        }
        doc_meta = SchemaColumnDocGenerator.build_doc_from_connector_schema(
            database="testdb",
            table_name="users",
            col_schema=col_schema,
        )
        doc = doc_meta["document"]
        meta = doc_meta["metadata"]

        assert "users" in doc
        assert "id" in doc
        assert "用户主键" in doc
        assert meta["database"] == "testdb"
        assert meta["is_primary_key"] is True
        assert meta["sample_values"] == "1|2|3"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

# ============================================================================
# Schema 列文档生成器
# ============================================================================
# 功能说明:
#   按 决策19 格式生成列文档用于向量化
#   同时生成完整 metadata（含 sample_values 用 | 连接）
# ============================================================================


from typing import Dict, List, Any


class SchemaColumnDocGenerator:
    """
    Schema 列文档生成器

    按决策19顺序：
        {table_name} {original_column_name} {column_name} {data_type}
        {column_description} {value_description} {data_format} {column_name}

    （末尾重复 column_name 一次做 boost）
    """

    def __init__(self):
        pass

    @staticmethod
    def format_column_document(
        table_name: str,
        original_column_name: str,
        column_name: str = None,
        column_description: str = None,
        value_description: str = None,
        **kwargs
    ) -> str:
        """
        生成列文档文本（用于向量化）

        格式: {table_name} | {original_column_name} | {desc}
        desc 优先级: column_description → value_description → column_name

        Args:
            table_name: 表名
            original_column_name: 原始列名
            column_name: 人类可读列名（回退用）
            column_description: 列描述
            value_description: 值描述
            **kwargs: 其他（忽略）

        Returns:
            str: 拼接后的文档文本
        """
        # desc 优先级: column_description → value_description → column_name
        desc = column_description or value_description or column_name or original_column_name or ""
        desc = desc.replace("\n", " ").replace("\r", " ").strip()

        parts = [p for p in [table_name, original_column_name, desc] if p]
        doc = " | ".join(parts).lower()
        return doc

    @staticmethod
    def build_column_metadata(
        database: str,
        table_name: str,
        original_column_name: str,
        column_name: str,
        data_type: str = None,
        column_description: str = None,
        value_description: str = None,
        data_format: str = None,
        is_primary_key: bool = False,
        is_foreign_key: bool = False,
        references: str = None,
        sample_values: List[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        生成列元数据（用于 ChromaDB metadata）

        Args:
            database: 数据库名/ID
            table_name: 表名
            original_column_name: 原始列名
            column_name: 人类可读列名
            data_type: 数据类型
            column_description: 列描述
            value_description: 值描述
            data_format: 数据格式
            is_primary_key: 是否主键
            is_foreign_key: 是否外键
            references: 外键引用（格式："table.column"）
            sample_values: 采样值列表（用 | 连接为字符串）
            **kwargs: 其他元数据（会合并进去）

        Returns:
            Dict[str, Any]: metadata 字典
        """
        meta = {
            "database": database,
            "table_name": table_name,
            "original_column_name": original_column_name,
            "column_name": column_name or original_column_name,
            "data_type": data_type or "TEXT",
            "column_description": column_description or "",
            "value_description": value_description or "",
            "data_format": data_format or "",
            "is_primary_key": is_primary_key,
            "is_foreign_key": is_foreign_key,
            "references": references or "",
        }

        if sample_values:
            # ChromaDB metadata 字符串类型，用 | 连接
            meta["sample_values"] = "|".join(str(v) for v in sample_values)
        else:
            meta["sample_values"] = ""

        if kwargs:
            meta.update(kwargs)

        return meta

    @staticmethod
    def build_doc_from_connector_schema(
        database: str,
        table_name: str,
        col_schema: Dict[str, Any],
        original_column_name: str = None
    ) -> Dict[str, Any]:
        """
        从 DatabaseConnector 返回的 schema 生成文档和 metadata

        Args:
            database: 数据库名/ID
            table_name: 表名
            col_schema: 列 schema 字典（来自 get_table_schema）
            original_column_name: 原始列名（默认用 col_schema["name"]）

        Returns:
            Dict[str, Any]: {"document": str, "metadata": dict}
        """
        col_name = col_schema.get("name", "")
        orig_col_name = original_column_name or col_name
        data_type = col_schema.get("type", "TEXT")
        desc = col_schema.get("description", "")
        column_name = col_schema.get("column_name", "") or desc or col_name
        value_desc = col_schema.get("value_description", "")
        data_format = col_schema.get("data_format", "")
        is_pk = col_schema.get("primary_key", False)

        sample_vals = col_schema.get("sample_values", [])

        # 外键信息（从 connector schema 解析）
        is_fk = col_schema.get("foreign_key", False) or col_schema.get("is_foreign_key", False)
        ref_tbl = col_schema.get("references_table", "")
        ref_col = col_schema.get("references_column", "")
        references = f"{ref_tbl}.{ref_col}" if ref_tbl and ref_col else ""

        doc = SchemaColumnDocGenerator.format_column_document(
            table_name=table_name,
            original_column_name=orig_col_name,
            column_name=column_name,
            data_type=data_type,
            column_description=desc,
            value_description=value_desc,
            data_format=data_format,
        )

        meta = SchemaColumnDocGenerator.build_column_metadata(
            database=database,
            table_name=table_name,
            original_column_name=orig_col_name,
            column_name=column_name,
            data_type=data_type,
            column_description=desc,
            value_description=value_desc,
            data_format=data_format,
            is_primary_key=is_pk,
            is_foreign_key=is_fk,
            references=references,
            sample_values=sample_vals,
        )

        return {"document": doc, "metadata": meta}

# ============================================================================
# Schema 选择器 - M-schema 格式转换和列相关性评估
# ============================================================================
# 功能说明:
#   1. 将检索到的 schema 转换为 M-schema 格式
#   2. 使用 LLM 评估每个列与用户查询的相关性
#   3. 过滤掉不相关的列，只保留生成 SQL 所需的最小列集
# ============================================================================


from dataclasses import dataclass, field
from typing import List, Dict, Any
from loguru import logger


@dataclass
class MSchemaColumn:
    """M-schema 格式的列定义"""
    name: str
    data_type: str
    description: str = ""
    sample_values: List[str] = field(default_factory=list)
    is_primary_key: bool = False
    is_foreign_key: bool = False
    references: str = ""
    relevance_score: float = None


@dataclass
class MSchemaTable:
    """M-schema 格式的表定义"""
    name: str
    columns: List[MSchemaColumn] = field(default_factory=list)
    description: str = ""
    row_count: int = 0


class MSchemaFormat:
    """M-schema 格式工具类"""

    @staticmethod
    def create_mschema_schema(tables: List[MSchemaTable]) -> Dict[str, Any]:
        """
        创建 M-schema 格式的整体 schema

        Args:
            tables: M-schema 表定义列表

        Returns:
            Dict[str, Any]: M-schema 字典表示
        """
        result = {
            "version": "1.0",
            "tables": [],
        }
        for tbl in tables:
            tbl_dict = {
                "name": tbl.name,
                "description": tbl.description,
                "row_count": tbl.row_count,
                "columns": [],
            }
            for col in tbl.columns:
                col_dict = {
                    "name": col.name,
                    "data_type": col.data_type,
                    "description": col.description,
                    "sample_values": col.sample_values,
                    "is_primary_key": col.is_primary_key,
                    "is_foreign_key": col.is_foreign_key,
                }
                if col.references:
                    col_dict["references"] = col.references
                if col.relevance_score is not None:
                    col_dict["relevance_score"] = col.relevance_score
                tbl_dict["columns"].append(col_dict)
            result["tables"].append(tbl_dict)
        return result

    @staticmethod
    def format_for_llm(mschema: Dict[str, Any]) -> str:
        """
        将 M-schema 格式化为适合 LLM 输入的文本

        Args:
            mschema: M-schema 字典

        Returns:
            str: 格式化后的文本 prompt
        """
        lines = []
        for tbl in mschema.get("tables", []):
            lines.append(f"# 表: {tbl['name']}")
            if tbl.get("description"):
                lines.append(f"  描述: {tbl['description']}")
            if tbl.get("row_count"):
                lines.append(f"  行数: {tbl['row_count']}")
            lines.append("  列:")
            for col in tbl.get("columns", []):
                flags = []
                if col.get("is_primary_key"):
                    flags.append("PK")
                if col.get("is_foreign_key"):
                    flags.append("FK")
                flag_str = f" [{','.join(flags)}]" if flags else ""

                ref_str = ""
                if col.get("references"):
                    ref_str = f" → {col['references']}"

                line = f"    - {col['name']} ({col['data_type']}){flag_str}{ref_str}"
                if col.get("description"):
                    line += f": {col['description']}"
                lines.append(line)

                if col.get("sample_values"):
                    samples = col["sample_values"][:3]
                    lines.append(f"      示例值: {samples}")
            lines.append("")

        return "\n".join(lines)


# Prompt 已迁移至 src/schema_selection/prompts.py
from src.schema_selection.prompts import COLUMN_RELEVANCE_PROMPT
from utils.llm_client import parse_json, stream_with_sse


class SchemaSelector:
    """
    Schema 选择器 - 基于 LLM 进行列相关性过滤

    Attributes:
        llm_client: LLM 客户端
        relevance_threshold: 相关性阈值（默认 0.5）
        db_connector: 数据库连接器（用于获取列详情）
    """

    def __init__(self, llm_client=None, relevance_threshold: float = 0.5,
                 db_connector=None):
        """
        初始化 Schema 选择器

        Args:
            llm_client: LLM 客户端实例
            relevance_threshold: 列相关性阈值
            db_connector: 数据库连接器实例
        """
        self.llm_client = llm_client
        self.relevance_threshold = relevance_threshold
        self.db_connector = db_connector

    def to_mschema(self, retrieved_context: Any) -> List[MSchemaTable]:
        """
        将检索上下文转换为 M-schema 格式

        Args:
            retrieved_context: RetrievedContext 对象

        Returns:
            List[MSchemaTable]: M-schema 表定义列表
        """
        # 按表分组
        table_columns: Dict[str, List[MSchemaColumn]] = {}
        table_meta: Dict[str, Dict] = {}

        # 收集所有涉及的表
        for tbl_item in retrieved_context.tables:
            if tbl_item.name not in table_columns:
                table_columns[tbl_item.name] = []
                table_meta[tbl_item.name] = {
                    "description": tbl_item.metadata.get("description", "") if tbl_item.metadata else "",
                    "row_count": tbl_item.metadata.get("row_count", 0) if tbl_item.metadata else 0,
                }

        # 处理列
        seen_columns = set()
        for col_item in retrieved_context.columns:
            tbl_name = col_item.table_name or "unknown"
            col_key = f"{tbl_name}.{col_item.name}"
            if col_key in seen_columns:
                continue
            seen_columns.add(col_key)

            if tbl_name not in table_columns:
                table_columns[tbl_name] = []
                table_meta[tbl_name] = {"description": "", "row_count": 0}

            meta = col_item.metadata or {}
            col = MSchemaColumn(
                name=col_item.name,
                data_type=meta.get("data_type", meta.get("column_type", "TEXT")),
                description=meta.get("description", ""),
                sample_values=meta.get("sample_values", []),
                is_primary_key=meta.get("is_primary_key", False),
                is_foreign_key=meta.get("is_foreign_key", False),
                references=meta.get("references", ""),
            )
            table_columns[tbl_name].append(col)

        # 如果有数据库连接器，进一步补充列信息
        if self.db_connector:
            for tbl_name in list(table_columns.keys()):
                try:
                    schema = self.db_connector.get_table_schema(tbl_name)
                    table_meta[tbl_name]["row_count"] = schema.get("row_count", 0)
                    existing_col_names = {c.name for c in table_columns[tbl_name]}
                    fk_map = {fk["column"]: f"{fk['references_table']}.{fk['references_column']}"
                              for fk in schema.get("foreign_keys", [])}

                    for col_info in schema.get("columns", []):
                        if col_info["name"] not in existing_col_names:
                            continue  # 只补充已检索到的列的详情
                        # 更新已有列的详细信息
                        for col in table_columns[tbl_name]:
                            if col.name == col_info["name"]:
                                col.data_type = col_info.get("type", col.data_type)
                                col.is_primary_key = col_info.get("primary_key", col.is_primary_key)
                                if col_info.get("description"):
                                    col.description = col_info["description"]
                                if col_info["name"] in fk_map:
                                    col.is_foreign_key = True
                                    col.references = fk_map[col_info["name"]]
                                samples = schema.get("sample_values", {}).get(col_info["name"], [])
                                if samples and not col.sample_values:
                                    col.sample_values = [str(s) for s in samples[:5]]
                                break
                except Exception as e:
                    logger.warning(f"获取表 {tbl_name} 详细信息失败: {e}")

        # 构建结果
        result = []
        for tbl_name, cols in table_columns.items():
            tbl = MSchemaTable(
                name=tbl_name,
                columns=cols,
                description=table_meta[tbl_name].get("description", ""),
                row_count=table_meta[tbl_name].get("row_count", 0),
            )
            result.append(tbl)

        return result

    def evaluate_column_relevance(self, mschema_tables: List[MSchemaTable],
                                   user_query: str) -> List[MSchemaTable]:
        """
        使用 LLM 评估每个列与用户查询的相关性

        Args:
            mschema_tables: M-schema 表定义列表
            user_query: 用户原始查询

        Returns:
            List[MSchemaTable]: 带相关性评分的表定义
        """
        if not self.llm_client:
            logger.warning("LLM 客户端未设置，所有列默认相关性 1.0")
            for tbl in mschema_tables:
                for col in tbl.columns:
                    col.relevance_score = 1.0
            return mschema_tables

        try:
            mschema_dict = MSchemaFormat.create_mschema_schema(mschema_tables)
            schema_text = MSchemaFormat.format_for_llm(mschema_dict)

            messages = COLUMN_RELEVANCE_PROMPT.format_messages(
                user_query=user_query,
                schema_text=schema_text,
            )
            raw = stream_with_sse(self.llm_client.stream(messages, as_json=True, temperature=0.0, thinking=False, run_name="ss-relevance"))
            result = parse_json(raw)

            # 应用评分
            scores_map = {}
            for entry in result.get("scores", []):
                key = f"{entry.get('table', '')}.{entry.get('column', '')}"
                scores_map[key] = entry.get("score", 0.0)

            for tbl in mschema_tables:
                for col in tbl.columns:
                    key = f"{tbl.name}.{col.name}"
                    col.relevance_score = scores_map.get(key, 0.0)

            logger.info(f"列相关性评估完成: {len(scores_map)} 个列已评分")

        except Exception as e:
            logger.error(f"列相关性评估失败: {e}，使用默认评分 1.0")
            for tbl in mschema_tables:
                for col in tbl.columns:
                    col.relevance_score = 1.0

        return mschema_tables

    def filter_columns(self, mschema_tables: List[MSchemaTable]) -> List[MSchemaTable]:
        """
        根据相关性评分过滤列

        Args:
            mschema_tables: 带评分的 M-schema 表定义

        Returns:
            List[MSchemaTable]: 过滤后的表定义
        """
        filtered = []
        for tbl in mschema_tables:
            kept_cols = []
            for col in tbl.columns:
                # 主键/外键即使分数低也保留（JOIN 需要）
                if col.is_primary_key or col.is_foreign_key:
                    kept_cols.append(col)
                    continue
                # 高于阈值的列保留
                if col.relevance_score is None or col.relevance_score >= self.relevance_threshold:
                    kept_cols.append(col)

            # 表至少保留一列才有意义
            if kept_cols:
                new_tbl = MSchemaTable(
                    name=tbl.name,
                    columns=kept_cols,
                    description=tbl.description,
                    row_count=tbl.row_count,
                )
                filtered.append(new_tbl)

        return filtered

    def select(self, retrieved_context: Any, user_query: str) -> List[MSchemaTable]:
        """
        完整的 Schema 选择流程

        Args:
            retrieved_context: IR 模块的检索上下文
            user_query: 用户查询

        Returns:
            List[MSchemaTable]: 精选后的 Schema
        """
        # 1. 转换为 M-schema
        mschema_tables = self.to_mschema(retrieved_context)
        logger.info(f"M-schema 转换完成: {len(mschema_tables)} 个表")

        # 2. LLM 评估相关性
        mschema_tables = self.evaluate_column_relevance(mschema_tables, user_query)

        # 3. 过滤低分列
        filtered = self.filter_columns(mschema_tables)
        logger.info(f"过滤后保留 {len(filtered)} 个表")

        return filtered

    # ------------------------------------------------------------------
    # LangGraph 子图接口（§18.4 / §18.8）
    # ------------------------------------------------------------------
    def build_graph(self):
        """
        返回 SS Agent 的已编译 LangGraph 子图

        子图节点：to_mschema → evaluate_relevance → filter_columns
        子图输入字段：user_query, retrieved_context
        子图输出字段：selected_schema (List[MSchemaTable])
        """
        from src.schema_selection.ss_graph import build_ss_graph
        return build_ss_graph(self)

# ============================================================================
# Schema 选择器 - M-schema 格式转换和列相关性评估
# ============================================================================
# 功能说明:
#   1. 将检索到的 schema 转换为 M-schema 格式（参考 CHESS 项目）
#   2. 使用 LLM 评估每个列与用户查询的相关性
#   3. 过滤掉不相关的列，只保留生成 SQL 所需的最小列集
#
# 输入:
#   - retrieved_context: 从 IR 模块获取的检索上下文
#   - user_query: 用户原始查询
#
# 输出:
#   - M-schema 格式的 schema 表示
#   - 精选后的列列表（用于 SQL 生成）
#
# 待您补充的细节:
#   1. M-schema 的具体格式定义（参考 C:\Users\WangHongze\Desktop\面试\智能问数\M-Schema-main\README.md）
#   2. LLM 评估列相关性的 prompt 设计
# ============================================================================


from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional


@dataclass
class MSchemaColumn:
    """M-schema 格式的列定义"""
    name: str                          # 列名
    data_type: str                     # 数据类型
    description: str = ""              # 列描述
    sample_values: List[str] = field(default_factory=list)  # 示例值
    is_primary_key: bool = False       # 是否主键
    is_foreign_key: bool = False       # 是否外键
    references: str = ""               # 外键引用，如 "orders.customer_id"
    relevance_score: float = None      # 与查询的相关性分数（LLM 评估）


@dataclass
class MSchemaTable:
    """M-schema 格式的表定义"""
    name: str                                     # 表名
    columns: List[MSchemaColumn] = field(default_factory=list)  # 列列表
    description: str = ""                         # 表描述
    row_count: int = 0                            # 行数（可选统计信息）


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
                           格式请参考 CHESS 项目中的 M-schema 规范

        TODO:
        - 将 MSchemaTable 对象转换为字典格式
        - 包含必要的元数据（版本号、创建时间等）
        """
        pass

    @staticmethod
    def format_for_llm(mschema: Dict[str, Any]) -> str:
        """
        将 M-schema 格式化为适合 LLM 输入的文本

        Args:
            mschema: M-schema 字典

        Returns:
            str: 格式化后的文本 prompt

        TODO:
        - 创建清晰的文本表示
        - 突出显示关键信息（主键、外键）
        - 控制长度避免超出 token 限制
        """
        pass


class SchemaSelector:
    """
    Schema 选择器 - 基于 LLM 进行列相关性过滤

    工作流程:
    ┌──────────────────┐
    │ RetrievedContext │  (来自 IR 模块)
    └────────┬─────────┘
             │
    ┌────────▼─────────┐
    │  转换为 M-schema  │
    └────────┬─────────┘
             │
    ┌────────▼─────────┐
    │ LLM 评估相关性    │  (few-shot 提示)
    └────────┬─────────┘
             │
    ┌────────▼─────────┐
    │  过滤低分列       │  (阈值：0.5)
    └────────┬─────────┘
             │
    ┌────────▼─────────┐
    │ 精选 Schema       │  (用于 SQL 生成)
    └──────────────────┘

    Attributes:
        llm_client: LLM 客户端
        relevance_threshold: 相关性阈值（默认 0.5）
    """

    def __init__(self, llm_client=None, relevance_threshold: float = 0.5):
        """
        初始化 Schema 选择器

        Args:
            llm_client: LLM 客户端实例
            relevance_threshold: 列相关性阈值
                               - 高于此值的列会被保留
                               - 可调低以增加召回，调高以提高精确率
        """
        self.llm_client = llm_client
        self.relevance_threshold = relevance_threshold

    def to_mschema(self, retrieved_context: Any) -> List[MSchemaTable]:
        """
        将检索上下文转换为 M-schema 格式

        Args:
            retrieved_context: RetrievedContext 对象（来自 IR 模块）

        Returns:
            List[MSchemaTable]: M-schema 表定义列表

        TODO:
        - 从 RetrievedContext 提取表和列信息
        - 构建 MSchemaColumn 和 MSchemaTable 对象
        - 填充样本值和外键信息
        """
        pass

    def evaluate_column_relevance(self, mschema_tables: List[MSchemaTable],
                                   user_query: str) -> List[MSchemaTable]:
        """
        使用 LLM 评估每个列与用户查询的相关性

        Args:
            mschema_tables: M-schema 表定义列表
            user_query: 用户原始查询

        Returns:
            List[MSchemaTable]: 带相关性评分的表定义

        TODO: 您需要设计 LLM prompt
        - 提供用户查询
        - 提供 M-schema 格式的 schema
        - 请求 LLM 为每个列打分（0-1）

        Prompt 示例:
        ```
        用户查询："{user_query}"

        可用 schema:
        {mschema_text}

        请为每个列评估与查询的相关性（0-1 分）：
        - 1.0: 直接用于查询条件或结果
        - 0.7: 间接相关，可能需要用于 JOIN
        - 0.3: 弱相关
        - 0.0: 完全不相关

        请以 JSON 格式返回评分结果。
        ```
        """
        pass

    def filter_columns(self, mschema_tables: List[MSchemaTable]) -> List[MSchemaTable]:
        """
        根据相关性评分过滤列

        Args:
            mschema_tables: 带评分的 M-schema 表定义

        Returns:
            List[MSchemaTable]: 过滤后的表定义（只保留高分列）

        TODO:
        - 移除相关性低于阈值的列
        - 但保留主键和外键列（即使评分低，JOIN 时需要）
        """
        pass

    def select(self, retrieved_context: Any, user_query: str) -> List[MSchemaTable]:
        """
        完整的 Schema 选择流程

        Args:
            retrieved_context: IR 模块的检索上下文
            user_query: 用户查询

        Returns:
            List[MSchemaTable]: 精选后的 Schema

        TODO: 完整流程
        1. 转换为 M-schema
        2. LLM 评估相关性
        3. 过滤低分列
        """
        pass
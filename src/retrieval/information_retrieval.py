# ============================================================================
# 信息检索 (IR) 模块
# ============================================================================
# 功能说明:
#   实现两阶段检索策略：
#   1. LSH 值检索 - 快速查找近似匹配的值
#   2. 语义 schema 检索 - 基于向量相似性查找相关表和列
#   然后合并两种检索结果，确保召回完整性
#
# 输入:
#   - user_query: 用户的自然语言查询
#   - lsh_indexer: LSH 索引器实例（用于值检索）
#   - vector_store: 向量存储管理器实例（用于 schema 检索）
#
# 输出:
#   - RetrievedContext 对象，包含检索到的表和列信息
#
# 待您补充的细节:
#   1. LLM 关键词提取的 prompt 设计
#   2. 两阶段检索的阈值调优
#   3. 结果的合并和去重策略
# ============================================================================


from dataclasses import dataclass
from typing import List, Dict, Any, Optional


@dataclass
class RetrievedItem:
    """检索结果项"""
    item_type: str  # 'table' | 'column' | 'value'
    name: str
    table_name: str = None
    score: float = 0.0  # 相似度分数
    metadata: Dict[str, Any] = None


@dataclass
class RetrievedContext:
    """检索上下文 - 整合所有检索结果"""
    tables: List[RetrievedItem] = None
    columns: List[RetrievedItem] = None
    values: List[RetrievedItem] = None
    keywords: List[str] = None

    def __post_init__(self):
        if self.tables is None:
            self.tables = []
        if self.columns is None:
            self.columns = []
        if self.values is None:
            self.values = []
        if self.keywords is None:
            self.keywords = []


class InformationRetrieval:
    """
    信息检索器 - 两阶段检索策略

    工作流程:
    ┌─────────────┐
    │ 用户查询     │
    └──────┬──────┘
           │
    ┌──────▼──────┐
    │ 关键词提取   │ → 使用 LLM + few-shot
    └──────┬──────┘
           │
    ┌──────┴──────┐
    │  两阶段检索  │
    │ ┌─────────┐ │
    │ │LSH 检索  │ │ → 值检索（精确/近似匹配）
    │ └─────────┘ │
    │ ┌─────────┐ │
    │ │语义检索  │ │ → Schema 检索（向量相似性）
    │ └─────────┘ │
    └──────┬──────┘
           │
    ┌──────▼──────┐
    │ 结果合并     │ → 并集策略 + 去重
    └──────┬──────┘
           │
    ┌──────▼──────┐
    │ RetrievedContext│
    └─────────────┘

    Attributes:
        llm_client: LLM 客户端（用于关键词提取）
        lsh_indexer: LSH 索引器
        vector_store: 向量存储管理器
    """

    def __init__(self, llm_client=None, lsh_indexer=None, vector_store=None):
        """
        初始化信息检索器

        Args:
            llm_client: LLM 客户端实例（用于关键词提取）
            lsh_indexer: LSH 索引器实例
            vector_store: 向量存储管理器实例

        TODO: 您需要实现的细节
        - 保存各组件引用
        - 配置 few-shot 示例（可从 CHESS 参考）
        """
        self.llm_client = llm_client
        self.lsh_indexer = lsh_indexer
        self.vector_store = vector_store

    def extract_keywords(self, query: str) -> List[str]:
        """
        从自然语言查询中提取关键词

        Args:
            query: 用户查询
                  例如："显示去年北京地区的销售额"

        Returns:
            List[str]: 提取的关键词列表
                      例如：["去年", "北京", "销售额"]

        TODO: 您需要使用 LLM 进行关键词提取
        - 构建 prompt，包含 few-shot 示例
        - 调用 Qwen API 获取关键词
        - 识别特殊类型：时间表达式、地点、度量单位等

        Prompt 示例:
        ```
        请从以下查询中提取关键检索词：

        示例 1:
        输入："显示 2023 年苹果公司的营收"
        输出：["2023 年", "苹果公司", "营收"]

        示例 2:
        输入："找出销售额超过 100 万的客户"
        输出：["销售额", "100 万", "客户"]

        输入："{query}"
        输出：
        ```
        """
        pass

    def retrieve_values(self, keywords: List[str], top_k: int = 5) -> List[RetrievedItem]:
        """
        使用 LSH 检索相似的值

        Args:
            keywords: 需要检索的关键词列表
            top_k: 每个关键词返回的前 k 个结果

        Returns:
            List[RetrievedItem]: 检索到的值列表

        TODO:
        - 对每个关键词使用 LSH 进行检索
        - 设置合适的相似度阈值（推荐 0.6-0.8）
        - 过滤掉低分结果
        - 去重后返回
        """
        pass

    def retrieve_schema(self, query: str, database_filter: str = None) -> Dict[str, List[RetrievedItem]]:
        """
        使用向量相似性检索相关的 schema

        Args:
            query: 用户原始查询（用于生成查询向量）
            database_filter: 可选的数据库过滤条件

        Returns:
            Dict[str, List[RetrievedItem]]: 检索到的表和列
                                           {
                                               "tables": [...],
                                               "columns": [...]
                                           }

        TODO:
        1. 使用 SchemaVectorizer 将查询转换为向量
        2. 在 ChromaDB 中进行相似性搜索
        3. 按相关性排序并返回前 n 个结果
        """
        pass

    def retrieve(self, query: str, database_filter: str = None) -> RetrievedContext:
        """
        执行完整的两阶段检索流程

        Args:
            query: 用户自然语言查询
            database_filter: 可选的数据库限定

        Returns:
            RetrievedContext: 整合的检索上下文

        TODO: 完整流程
        1. 关键词提取
        2. LSH 值检索
        3. 语义 schema 检索
        4. 合并结果（并集策略）
        5. 返回 RetrievedContext
        """
        pass

    def enhance_with_schema(self, context: RetrievedContext) -> RetrievedContext:
        """
        根据检索到的值，补充相关的 schema 信息

        Args:
            context: 当前检索上下文

        Returns:
            RetrievedContext: 增强后的上下文
                           （如果某个值被检索到，确保其所属列也被包含）

        TODO:
        - 对于每个检索到的值，找到其对应的表/列
        - 将这些表/列添加到上下文中
        - 这确保 schema 完整性
        """
        pass
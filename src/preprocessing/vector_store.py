# ============================================================================
# 向量存储管理器
# ============================================================================
# 功能说明:
#   使用 ChromaDB 管理 schema 嵌入向量的存储和检索
#   提供持久化、增量更新和高效相似性搜索能力
#
# 输入:
#   - collection_name: 集合名称（用于区分不同数据集）
#   - persist_directory: 持久化存储目录
#
# 输出:
#   - 可向量化数据并存储到 ChromaDB
#   - 可执行相似性检索
#
# 待您补充的细节:
#   1. ChromaDB 客户端初始化（本地模式）
#   2. Collection 的创建和管理
#   3. 向量的增删改查操作
# ============================================================================


from typing import List, Dict, Any


class VectorStoreManager:
    """
    向量存储管理器 - 基于 ChromaDB 实现

    ChromaDB 特性:
    - 轻量级，无需独立服务（本地模式）
    - 支持持久化存储
    - 内置相似性搜索（cosine, l2, ip）
    - 支持元数据过滤

    Attributes:
        client: ChromaDB 客户端实例
        collection: 当前使用的集合
        persist_directory: 持久化存储路径
    """

    def __init__(self, collection_name: str = "nl2sql_schemas",
                 persist_directory: str = "./vector_store"):
        """
        初始化向量存储管理器

        Args:
            collection_name: 集合名称
                           用于区分不同的向量集合
                           例如："bird_sql_schemas", "custom_db_schemas"
            persist_directory: 持久化存储目录
                            向量数据会保存在此目录
                            建议使用绝对路径

        TODO: 您需要实现的细节
        - 初始化 ChromaDB 客户端（PersistentClient）
        - 获取或创建 collection
        - 设置合适的 distance_metric（推荐 cosine）
        """
        self.collection_name = collection_name
        self.persist_directory = persist_directory
        self.client = None
        self.collection = None

        # TODO: 初始化客户端和 collection
        # import chromadb
        # from chromadb.config import Settings
        # self.client = chromadb.PersistentClient(
        #     path=persist_directory,
        #     settings=Settings(anonymized_telemetry=False)
        # )
        # self.collection = self.client.get_or_create_collection(
        #     name=collection_name,
        #     metadata={"hnsw:space": "cosine"}
        # )

    def add_embeddings(self, embeddings: List[Dict[str, Any]]) -> bool:
        """
        添加向量 embedding 到存储

        Args:
            embeddings: Embedding 列表
                       [
                           {
                               "id": "table_orders_col_id",
                               "embedding": [0.1, 0.2, ...],  # 向量
                               "metadata": {
                                   "table_name": "orders",
                                   "column_name": "id",
                                   "column_type": "INT",
                                   "database": "bird_sql_001"
                               },
                               "document": "订单表中的 id 字段，INT 类型，表示订单 ID"
                           },
                           ...
                       ]

        Returns:
            bool: 添加成功返回 True，失败返回 False

        TODO:
        - 分批添加避免单次请求过大
        - 检查 ID 是否重复，重复则更新
        - 添加进度显示
        """
        pass

    def query(self, query_embedding: List[float],
              n_results: int = 10,
              where_filter: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """
        查询最相似的向量

        Args:
            query_embedding: 查询向量
            n_results: 返回结果数量
            where_filter: 元数据过滤条件（可选）
                         例如：{"table_name": "orders", "database": "bird_sql_001"}

        Returns:
            List[Dict[str, Any]]: 相似结果列表
                                 [
                                     {
                                         "id": "...",
                                         "metadata": {...},
                                         "document": "...",
                                         "distance": 0.15  # 相似度距离（越小越相似）
                                     },
                                     ...
                                 ]

        TODO:
        - 执行向量相似性搜索
        - 应用元数据过滤
        - 按距离排序返回结果
        """
        pass

    def delete_by_database(self, database_name: str) -> bool:
        """
        删除指定数据库的所有向量

        Args:
            database_name: 数据库名称

        Returns:
            bool: 删除成功返回 True

        用途：当某个数据库被移除时清理向量存储

        TODO:
        - 使用 where 条件过滤并删除
        """
        pass

    def get_all_tables(self, database_name: str = None) -> List[str]:
        """
        获取所有已索引的表名

        Args:
            database_name: 可选，限定特定数据库

        Returns:
            List[str]: 表名列表

        TODO:
        - 遍历 collection 中的所有条目
        - 提取唯一的 table_name
        """
        pass

    def clear(self):
        """
        清空当前集合中的所有数据

        TODO:
        - 删除当前 collection
        - 重新创建空 collection
        """
        pass

    def get_stats(self) -> Dict[str, Any]:
        """
        获取向量存储的统计信息

        Returns:
            Dict[str, Any]: 统计信息
                           {
                               "total_embeddings": 1234,
                               "unique_tables": 56,
                               "databases": ["db1", "db2", ...],
                               ...
                           }

        TODO:
        - 计算各种统计指标
        """
        pass
# ============================================================================
# 向量存储管理器
# ============================================================================
# 功能说明:
#   使用 ChromaDB 管理 schema 嵌入向量的存储和检索
#   提供持久化、增量更新和高效相似性搜索能力
# ============================================================================


from typing import List, Dict, Any, Optional
from loguru import logger

import chromadb
from chromadb.config import Settings


class VectorStoreManager:
    """
    向量存储管理器 - 基于 ChromaDB 实现

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
            persist_directory: 持久化存储目录
        """
        self.collection_name = collection_name
        self.persist_directory = persist_directory
        self.client = None
        self.collection = None
        self._init_client()

    def _init_client(self):
        """初始化 ChromaDB 客户端和 collection"""
        try:
            self.client = chromadb.PersistentClient(
                path=self.persist_directory,
                settings=Settings(anonymized_telemetry=False),
            )
            self.collection = self.client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            logger.info(
                f"ChromaDB 初始化成功: collection={self.collection_name}, "
                f"现有条目={self.collection.count()}"
            )
        except Exception as e:
            logger.error(f"ChromaDB 初始化失败: {e}")
            raise

    def add_embeddings(self, embeddings: List[Dict[str, Any]]) -> bool:
        """
        添加向量 embedding 到存储

        Args:
            embeddings: Embedding 列表，每项包含 id, embedding, metadata, document

        Returns:
            bool: 添加成功返回 True
        """
        if not embeddings:
            return True

        try:
            batch_size = 500
            for i in range(0, len(embeddings), batch_size):
                batch = embeddings[i : i + batch_size]
                ids = [e["id"] for e in batch]
                vectors = [e["embedding"] for e in batch]
                metas = [e.get("metadata", {}) for e in batch]
                docs = [e.get("document", "") for e in batch]

                # upsert 实现「重复则更新」
                self.collection.upsert(
                    ids=ids,
                    embeddings=vectors,
                    metadatas=metas,
                    documents=docs,
                )

            logger.info(f"添加 {len(embeddings)} 条 embedding 成功")
            return True

        except Exception as e:
            logger.error(f"添加 embedding 失败: {e}")
            return False

    def query(self, query_embedding: List[float],
              n_results: int = 10,
              where_filter: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """
        查询最相似的向量

        Args:
            query_embedding: 查询向量
            n_results: 返回结果数量
            where_filter: 元数据过滤条件

        Returns:
            List[Dict]: 相似结果列表
        """
        try:
            kwargs = {
                "query_embeddings": [query_embedding],
                "n_results": n_results,
            }
            if where_filter:
                kwargs["where"] = where_filter

            results = self.collection.query(**kwargs)

            items = []
            if results and results["ids"] and results["ids"][0]:
                for i in range(len(results["ids"][0])):
                    item = {
                        "id": results["ids"][0][i],
                        "metadata": (
                            results["metadatas"][0][i]
                            if results["metadatas"]
                            else {}
                        ),
                        "document": (
                            results["documents"][0][i]
                            if results["documents"]
                            else ""
                        ),
                        "distance": (
                            results["distances"][0][i]
                            if results["distances"]
                            else None
                        ),
                    }
                    items.append(item)

            return items

        except Exception as e:
            logger.error(f"向量查询失败: {e}")
            return []

    def delete_by_database(self, database_name: str) -> bool:
        """
        删除指定数据库的所有向量

        Args:
            database_name: 数据库名称

        Returns:
            bool: 删除成功返回 True
        """
        try:
            self.collection.delete(
                where={"database": database_name}
            )
            logger.info(f"已删除数据库 {database_name} 的所有向量")
            return True
        except Exception as e:
            logger.error(f"删除向量失败: {e}")
            return False

    def get_all_tables(self, database_name: str = None) -> List[str]:
        """
        获取所有已索引的表名

        Args:
            database_name: 可选，限定特定数据库

        Returns:
            List[str]: 表名列表
        """
        try:
            where = {}
            if database_name:
                where["database"] = database_name

            kwargs = {"include": ["metadatas"]}
            if where:
                kwargs["where"] = where

            results = self.collection.get(**kwargs)

            tables = set()
            if results and results["metadatas"]:
                for meta in results["metadatas"]:
                    if meta and "table_name" in meta:
                        tables.add(meta["table_name"])

            return sorted(tables)

        except Exception as e:
            logger.error(f"获取表名列表失败: {e}")
            return []

    def clear(self):
        """清空当前集合中的所有数据"""
        try:
            self.client.delete_collection(self.collection_name)
            self.collection = self.client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            logger.info(f"集合 {self.collection_name} 已清空")
        except Exception as e:
            logger.error(f"清空集合失败: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """
        获取向量存储的统计信息

        Returns:
            Dict[str, Any]: 统计信息
        """
        try:
            total = self.collection.count()
            results = self.collection.get(include=["metadatas"])

            tables = set()
            databases = set()
            if results and results["metadatas"]:
                for meta in results["metadatas"]:
                    if meta:
                        if "table_name" in meta:
                            tables.add(meta["table_name"])
                        if "database" in meta:
                            databases.add(meta["database"])

            return {
                "total_embeddings": total,
                "unique_tables": len(tables),
                "databases": sorted(databases),
                "collection_name": self.collection_name,
            }
        except Exception as e:
            logger.error(f"获取统计信息失败: {e}")
            return {"total_embeddings": 0}

# ============================================================================
# Schema 向量化模块
# ============================================================================
# 功能说明:
#   使用 BGE-M3 模型将表名、列名、数据类型和描述信息转换为向量嵌入
#   这些向量用于后续的语义相似性检索
#
# 输入:
#   - schema_info: schema 元数据字典
#   - model_name: embedding 模型名称，默认 "BAAI/bge-m3"
#
# 输出:
#   - 每个 schema 元素的向量表示
#   - 可用于 ChromaDB 等向量数据库存储
# ============================================================================


from typing import List, Dict, Any, Optional
from loguru import logger


class SchemaVectorizer:
    """
    Schema 向量化器 - 使用 BGE-M3 模型生成语义嵌入

    BGE-M3 模型特性:
    - 支持多语言（包括中文）
    - 多功能：dense + sparse + colbert 三种检索模式
    - 长文本支持（8192 tokens）

    Attributes:
        model_name (str): BGE-M3 模型名称
        model: 加载的 embedding 模型实例
        device (str): 运行设备 ('cuda' | 'cpu')
    """

    def __init__(self, model_name: str = "D:/Models/bge-m3", device: str = None):
        """
        初始化向量化器

        Args:
            model_name: Embedding 模型名称
            device: 运行设备 ('cuda' | 'cpu' | None 自动选择)
        """
        self.model_name = model_name
        self.device = device
        self.model = None

    def load_model(self):
        """
        加载 embedding 模型

        使用 FlagEmbedding 的 FlagModel 加载 BGE-M3，
        支持 FP16 加速和自动设备选择。

        Raises:
            ImportError: FlagEmbedding 未安装
            RuntimeError: 模型加载失败
        """
        if self.model is not None:
            logger.info("模型已加载，跳过重复初始化")
            return

        try:
            # 优先尝试 sentence-transformers（Windows 下更稳定）
            # 如失败再回退到 FlagEmbedding.BGEM3FlagModel
            try:
                from sentence_transformers import SentenceTransformer
                logger.info(f"使用 sentence-transformers 加载 BGE-M3...")
                self.model = SentenceTransformer(
                    self.model_name,
                    device=self.device or "cpu",
                )
                self._backend = "sentence_transformers"
                logger.info(f"BGE-M3 模型加载成功 (sentence-transformers): {self.model_name}")
                return
            except Exception as e1:
                logger.warning(f"sentence-transformers 加载失败: {e1}，尝试 FlagEmbedding...")

            from FlagEmbedding import BGEM3FlagModel

            use_fp16 = self.device != "cpu"
            self.model = BGEM3FlagModel(
                self.model_name,
                use_fp16=use_fp16,
                device=self.device or None,
            )
            self._backend = "flag_embedding"
            logger.info(f"BGE-M3 模型加载成功 (FlagEmbedding): {self.model_name}")

        except ImportError:
            raise ImportError(
                "请安装: pip install sentence-transformers 或 pip install FlagEmbedding"
            )
        except Exception as e:
            raise RuntimeError(f"模型加载失败: {e}")

    def embed_texts(
        self,
        texts: List[str],
        return_dense: bool = True,
        return_sparse: bool = False,
        return_colbert: bool = False,
        batch_size: int = 32,
    ) -> Dict[str, List]:
        """
        对文本列表进行向量化

        Args:
            texts: 需要向量化的一组文本
            return_dense: 是否返回 dense embedding
            return_sparse: 是否返回 sparse embedding
            return_colbert: 是否返回 colbert embedding
            batch_size: 批处理大小

        Returns:
            Dict[str, List]: 包含不同类型向量的字典

        Raises:
            RuntimeError: 模型未加载
        """
        if self.model is None:
            raise RuntimeError("模型未加载，请先调用 load_model()")

        if not texts:
            return {"dense": []}

        backend = getattr(self, "_backend", "flag_embedding")

        if backend == "sentence_transformers":
            # sentence-transformers 只支持 dense
            if return_sparse or return_colbert:
                logger.warning("sentence-transformers backend 不支持 sparse/colbert，仅返回 dense")
            embeddings = self.model.encode(
                texts,
                batch_size=batch_size,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            return {"dense": embeddings.tolist()}

        # FlagEmbedding backend
        result = self.model.encode(
            texts,
            return_dense=return_dense,
            return_sparse=return_sparse,
            return_colbert=return_colbert,
            batch_size=batch_size,
        )

        output = {}
        if return_dense:
            output["dense"] = result["dense_vecs"].tolist()
        if return_sparse:
            output["sparse"] = result["lexical_weights"]
        if return_colbert:
            output["colbert"] = result["colbert_vecs"].tolist()

        return output

    def embed_schema(self, schema_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        对整个 schema 进行向量化

        Args:
            schema_info: Schema 信息字典，包含 table_name, columns 等

        Returns:
            Dict[str, Any]: 带嵌入向量的 schema 信息
        """
        if self.model is None:
            raise RuntimeError("模型未加载，请先调用 load_model()")

        table_name = schema_info.get("table_name", "")
        columns = schema_info.get("columns", [])

        # 构建所有需要向量化的文本
        texts = []
        text_meta = []

        # 表名
        table_desc = table_name
        texts.append(table_desc)
        text_meta.append({"type": "table", "table_name": table_name})

        # 每个列
        for col in columns:
            col_text = self.format_column_description(col, table_name)
            texts.append(col_text)
            text_meta.append({
                "type": "column",
                "table_name": table_name,
                "column_name": col.get("name", ""),
            })

        # 批量向量化
        embeddings = self.embed_texts(texts, return_dense=True)

        # 组装结果
        result = {
            "table_name": table_name,
            "table_embedding": embeddings["dense"][0] if embeddings["dense"] else None,
            "columns": [],
        }

        for i, col in enumerate(columns):
            col_result = dict(col)
            col_result["embedding"] = (
                embeddings["dense"][i + 1] if i + 1 < len(embeddings["dense"]) else None
            )
            col_result["full_description_embedding"] = col_result["embedding"]
            result["columns"].append(col_result)

        return result

    def get_embedding_dimension(self) -> int:
        """
        获取模型的向量维度

        Returns:
            int: Dense embedding 的维度（BGE-M3 为 1024）
        """
        if self.model is not None:
            try:
                return self.model.model.config.hidden_size
            except Exception:
                pass
        return 1024  # BGE-M3 默认维度

    @staticmethod
    def format_column_description(column: dict, table_name: str = None) -> str:
        """
        格式化列的描述文本，用于向量化

        优先级策略：
        1. 优先使用 column["description"]
        2. 回退到拼接基本信息（列名 + 类型）

        Args:
            column: 列信息字典
            table_name: 所属表名

        Returns:
            str: 格式化的描述文本
        """
        # 优先使用已有的 description
        if column.get("description"):
            desc = column["description"]
            if table_name:
                return f"{table_name}表中的{desc}"
            return desc

        # 回退到基本信息拼接
        col_name = column.get("name", "")
        col_type = column.get("type", "TEXT")

        parts = []
        if table_name:
            parts.append(f"{table_name}表")
        parts.append(f"{col_name}字段")
        parts.append(f"类型为{col_type}")

        return "，".join(parts)

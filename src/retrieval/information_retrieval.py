# ============================================================================
# 信息检索 (IR) 模块
# ============================================================================
# 功能说明:
#   实现两阶段检索策略：
#   1. LSH 值检索 - 快速查找近似匹配的值
#   2. 语义 schema 检索 - 基于向量相似性查找相关表和列
#   然后合并两种检索结果，确保召回完整性
# ============================================================================


import json
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from loguru import logger

# 模块级导入：测试时方便 patch
try:
    from src.preprocessing.lsh_index import LSHIndexer
except ImportError:
    LSHIndexer = None


@dataclass
class RetrievedItem:
    """检索结果项"""
    item_type: str  # 'table' | 'column' | 'value'
    name: str
    table_name: str = None
    score: float = 0.0  # 相似度分数
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class RetrievedContext:
    """检索上下文 - 整合所有检索结果"""
    tables: List[RetrievedItem] = None
    columns: List[RetrievedItem] = None
    values: List[RetrievedItem] = None
    keywords: List[str] = None
    # 3.5 新增：检索元数据
    lsh_hit_count: int = 0           # LSH 命中数量
    vector_top_scores: List[float] = field(default_factory=list)  # 向量检索 top_k 分数列表

    def __post_init__(self):
        if self.tables is None:
            self.tables = []
        if self.columns is None:
            self.columns = []
        if self.values is None:
            self.values = []
        if self.keywords is None:
            self.keywords = []

    def get_all_table_names(self) -> List[str]:
        """获取所有涉及的表名（去重）"""
        names = set()
        for t in self.tables:
            if t.name:
                names.add(t.name)
        for c in self.columns:
            if c.table_name:
                names.add(c.table_name)
        for v in self.values:
            if v.table_name:
                names.add(v.table_name)
        return sorted(names)


# ============================================================================
# 关键词提取 Prompt
# ============================================================================

KEYWORD_EXTRACTION_PROMPT = """你是一个专业的数据库查询分析专家。请从用户查询中提取用于数据库检索的关键词。

提取规则：
1. 提取可能与数据库字段名或值匹配的关键词
2. 时间表达式保留原样（如"去年"、"2023年"）
3. 度量词保留（如"销售额"、"利润"）
4. 实体名称保留（如"苹果"、"北京"）
5. 不要提取常见停用词（如"显示"、"查询"、"找出"等动词）
6. 返回 JSON 格式：{{"keywords": ["关键词1", "关键词2", ...]}}

示例：
输入："显示去年北京地区的销售额"
输出：{{"keywords": ["去年", "北京", "销售额"]}}

输入："找出销售额超过100万的客户"
输出：{{"keywords": ["销售额", "100万", "客户"]}}

输入："苹果公司的营收增长情况"
输出：{{"keywords": ["苹果公司", "营收", "增长"]}}

输入："{query}"
输出："""


class InformationRetrieval:
    """
    信息检索器 - 两阶段检索策略

    Attributes:
        llm_client: LLM 客户端
        lsh_indexer: LSH 索引器
        vector_store: 向量存储管理器
    """

    def __init__(self, llm_client=None, lsh_indexer=None, vector_store=None,
                 lsh_threshold: float = 0.6, vector_top_k: int = 10):
        """
        初始化信息检索器

        Args:
            llm_client: LLM 客户端实例
            lsh_indexer: LSH 索引器实例
            vector_store: 向量存储管理器实例
            lsh_threshold: LSH 检索相似度阈值
            vector_top_k: 向量检索返回数量
        """
        self.llm_client = llm_client
        self.lsh_indexer = lsh_indexer
        self.vector_store = vector_store
        self.lsh_threshold = lsh_threshold
        self.vector_top_k = vector_top_k

    def extract_keywords(self, query: str) -> List[str]:
        """
        从自然语言查询中提取关键词

        Args:
            query: 用户查询

        Returns:
            List[str]: 提取的关键词列表
        """
        if not self.llm_client:
            logger.warning("LLM 客户端未设置，使用简单分词回退")
            return self._simple_keyword_extract(query)

        try:
            prompt = KEYWORD_EXTRACTION_PROMPT.format(query=query)
            messages = [
                {"role": "system", "content": "你是数据库查询分析专家，只输出 JSON。"},
                {"role": "user", "content": prompt},
            ]
            result = self.llm_client.chat_json(messages, temperature=0.0)
            keywords = result.get("keywords", [])
            logger.info(f"关键词提取结果: {keywords}")
            return keywords

        except Exception as e:
            logger.warning(f"LLM 关键词提取失败，使用回退方案: {e}")
            return self._simple_keyword_extract(query)

    @staticmethod
    def _simple_keyword_extract(query: str) -> List[str]:
        """
        简单关键词提取回退方案（不依赖 LLM）

        去除常见停用词，对中文使用 jieba 分词（可选），
        回退时按字符 n-gram + 空格分割
        """
        import re

        # 停用词
        stopwords = {"显示", "查询", "找出", "列出", "统计", "计算", "给我", "帮我",
                      "看", "的", "了", "是", "在", "和", "与", "有", "中", "一",
                      "一下", "那些", "哪些", "什么", "怎么", "如何",
                      "show", "find", "get", "list", "the", "a", "an", "of", "in"}

        # 尝试使用 jieba 分词
        try:
            import jieba
            tokens = list(jieba.cut(query))
        except ImportError:
            # 回退：按空格/标点分割，中文整体作为单个 token
            tokens = re.split(r'[\s,，。；;！!？?、]+', query)
            if len(tokens) <= 1 and len(query) > 2:
                # 如果整个中文查询是一个 token，按 2-4 字符切分
                tokens = [query[i:i+4] for i in range(0, len(query), 4)]

        keywords = [t.strip() for t in tokens
                    if t.strip() and t.strip() not in stopwords and len(t.strip()) > 1]
        return keywords

    def retrieve_values(self, keywords: List[str], top_k: int = 5,
                      value_semantic_threshold: float = 0.6) -> List[RetrievedItem]:
        """
        LSH 粗召回 + 语义精排两阶段值检索

        Args:
            keywords: 需要检索的关键词列表
            top_k: 每个关键词 LSH 粗召回前 k 个结果
            value_semantic_threshold: 语义精排的余弦相似度阈值（默认 0.6）

        Returns:
            List[RetrievedItem]: 检索到的值列表，带有 lsh_jaccard_score 和 semantic_score
        """
        if not self.lsh_indexer:
            logger.warning("LSH 索引器未设置，跳过值检索")
            return []

        # 检查是否有向量器用于语义精排（没有就降级到只有 LSH）
        has_semantic = False
        if hasattr(self, "_vectorizer") and self._vectorizer is not None:
            if self._vectorizer.model is not None:
                has_semantic = True

        all_items: List[RetrievedItem] = []
        seen = set()

        for keyword in keywords:
            try:
                if not hasattr(self.lsh_indexer, '_loaded_lsh') or self.lsh_indexer._loaded_lsh is None:
                    continue

                # 阶段1：LSH 粗召回
                results = self.lsh_indexer.query(
                    self.lsh_indexer._loaded_lsh,
                    self.lsh_indexer._loaded_minhashes,
                    keyword,
                    top_k=top_k,
                )

                candidates_to_rerank = []

                for table_name, columns in results.items():
                    for col_name, values in columns.items():
                        for val in values:
                            key = f"{table_name}.{col_name}.{val}"
                            if key in seen:
                                continue
                            seen.add(key)

                            # LSH 相似度分数
                            val_mh = LSHIndexer.create_minhash(val)
                            kw_mh = LSHIndexer.create_minhash(keyword)
                            lsh_score = LSHIndexer.jaccard_similarity(kw_mh, val_mh)

                            if lsh_score < self.lsh_threshold:
                                continue

                            if has_semantic:
                                candidates_to_rerank.append({
                                    "keyword": keyword,
                                    "value": val,
                                    "table_name": table_name,
                                    "col_name": col_name,
                                    "lsh_score": lsh_score,
                                })
                            else:
                                # 降级：只看 LSH
                                all_items.append(RetrievedItem(
                                    item_type="value",
                                    name=val,
                                    table_name=table_name,
                                    score=lsh_score,
                                    metadata={
                                        "column_name": col_name,
                                        "lsh_jaccard_score": lsh_score,
                                        "semantic_score": None,
                                    },
                                ))

                # 阶段2：语义精排（如果有向量器）
                if has_semantic and candidates_to_rerank:
                    # 准备需要 embed 的文本：[(keyword + " " + value), ...]
                    texts_to_embed = []
                    for cand in candidates_to_rerank:
                        text = f"{cand['keyword']} {cand['value']}"
                        texts_to_embed.append(text)

                    # 批量 embed
                    embeddings = self._vectorizer.embed_texts(texts_to_embed, return_dense=True)
                    dense_vectors = embeddings.get("dense", [])

                    # 每个 candidate 单独比较（keyword 和 value 分别 embed 后算余弦相似度）
                    # 注：为了简单，我们把 keyword 和 value 单独 embed 后计算
                    # 优化：先单独 embed 所有 keyword 和 value
                    kw_list = [c["keyword"] for c in candidates_to_rerank]
                    val_list = [c["value"] for c in candidates_to_rerank]

                    kw_embedding_result = self._vectorizer.embed_texts(kw_list, return_dense=True)
                    val_embedding_result = self._vectorizer.embed_texts(val_list, return_dense=True)
                    kw_vectors = kw_embedding_result.get("dense", [])
                    val_vectors = val_embedding_result.get("dense", [])

                    for i, cand in enumerate(candidates_to_rerank):
                        if i >= len(kw_vectors) or i >= len(val_vectors):
                            continue

                        # 余弦相似度计算
                        kw_vec = kw_vectors[i]
                        val_vec = val_vectors[i]
                        dot = sum(a * b for a, b in zip(kw_vec, val_vec))
                        norm_kw = (sum(a * a for a in kw_vec)) ** 0.5
                        norm_val = (sum(a * a for a in val_vec)) ** 0.5
                        if norm_kw == 0 or norm_val == 0:
                            semantic_score = 0.0
                        else:
                            semantic_score = dot / (norm_kw * norm_val)

                        if semantic_score < value_semantic_threshold:
                            continue

                        # 最终 score 可以加权或直接取 semantic
                        final_score = semantic_score

                        all_items.append(RetrievedItem(
                            item_type="value",
                            name=cand["value"],
                            table_name=cand["table_name"],
                            score=final_score,
                            metadata={
                                "column_name": cand["col_name"],
                                "lsh_jaccard_score": cand["lsh_score"],
                                "semantic_score": semantic_score,
                            },
                        ))

            except Exception as e:
                logger.warning(f"值检索关键词 '{keyword}' 失败: {e}")
                import traceback
                logger.debug(traceback.format_exc())
                continue

        # 按最终 score 排序
        all_items.sort(key=lambda x: x.score, reverse=True)
        logger.info(f"值检索: LSH + 语义精排共 {len(all_items)} 个结果")
        return all_items

    def retrieve_schema(self, keywords: List[str], database_filter: str = None,
                      column_top_k_per_keyword: int = 5) -> Dict[str, List[RetrievedItem]]:
        """
        使用向量相似性检索相关的 schema（每个 keyword 独立查询后合并）

        Args:
            keywords: 关键词列表（每个 keyword 独立查询一次）
            database_filter: 可选的数据库过滤条件
            column_top_k_per_keyword: 每个 keyword 查 top_k 个列（默认 5）

        Returns:
            Dict[str, List[RetrievedItem]]: 检索到的表和列
        """
        if not self.vector_store:
            logger.warning("向量存储未设置，跳过 schema 检索")
            return {"tables": [], "columns": []}

        if not hasattr(self, "_vectorizer") or self._vectorizer is None:
            logger.warning("向量化器未设置，跳过 schema 检索")
            return {"tables": [], "columns": []}

        if not keywords:
            return {"tables": [], "columns": []}

        try:
            # 1. 逐个 keyword 查询向量
            all_results = []
            for kw in keywords:
                # 向量化单个 keyword
                embedding_result = self._vectorizer.embed_texts([kw], return_dense=True)
                query_vec = embedding_result["dense"][0]

                where_filter = None
                if database_filter:
                    where_filter = {"database": database_filter}

                # 查询
                results_for_kw = self.vector_store.query(
                    query_embedding=query_vec,
                    n_results=column_top_k_per_keyword,
                    where_filter=where_filter,
                )
                all_results.extend(results_for_kw)

            # 2. 合并去重（同一 table.column 多次命中取最高 score）
            column_score_map = {}
            for r in all_results:
                meta = r.get("metadata", {})
                dist = r.get("distance", 1.0)
                score = 1.0 - dist if dist is not None else 0.0

                table_name = meta.get("table_name", "")
                col_name = meta.get("original_column_name", meta.get("column_name", ""))
                if not table_name or not col_name:
                    continue

                key = f"{table_name}.{col_name}"
                if key not in column_score_map or score > column_score_map[key]["score"]:
                    column_score_map[key] = {
                        "score": score,
                        "metadata": meta,
                        "document": r.get("document", ""),
                        "distance": dist,
                    }

            # 3. 转成 RetrievedItem 列表，按 score 降序
            sorted_items = sorted(column_score_map.items(), key=lambda x: x[1]["score"], reverse=True)
            columns = []
            seen_tables = set()
            tables = []

            for key, info in sorted_items:
                table_name = info["metadata"].get("table_name", "")
                col_name = info["metadata"].get("original_column_name",
                                                 info["metadata"].get("column_name", ""))

                # 列 item
                col_item = RetrievedItem(
                    item_type="column",
                    name=col_name,
                    table_name=table_name,
                    score=info["score"],
                    metadata=info["metadata"],
                )
                columns.append(col_item)

                # 所属的表也加入（score 取该列 score）
                if table_name and table_name not in seen_tables:
                    seen_tables.add(table_name)
                    tables.append(RetrievedItem(
                        item_type="table",
                        name=table_name,
                        table_name=table_name,
                        score=info["score"],
                        metadata={"database": info["metadata"].get("database", "")},
                    ))

            logger.info(f"语义 schema 检索: {len(tables)} 个表, {len(columns)} 个列（关键词数: {len(keywords)}）")
            return {"tables": tables, "columns": columns}

        except Exception as e:
            logger.error(f"语义 schema 检索失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return {"tables": [], "columns": []}

    def retrieve(self, query: str, database_filter: str = None) -> RetrievedContext:
        """
        执行完整的两阶段检索流程

        Args:
            query: 用户自然语言查询
            database_filter: 可选的数据库限定

        Returns:
            RetrievedContext: 整合的检索上下文
        """
        logger.info(f"开始检索: query='{query}'")

        # 1. 关键词提取
        keywords = self.extract_keywords(query)
        logger.info(f"提取关键词: {keywords}")

        # 2. LSH 值检索
        value_items = self.retrieve_values(keywords)

        # 3. 语义 schema 检索（用 keywords 逐个查询）
        schema_results = self.retrieve_schema(keywords, database_filter)

        # 4. 合并结果
        context = RetrievedContext(
            tables=schema_results.get("tables", []),
            columns=schema_results.get("columns", []),
            values=value_items,
            keywords=keywords,
            lsh_hit_count=len(value_items),
            vector_top_scores=[c.score for c in schema_results.get("columns", [])],
        )

        # 5. 根据检索到的值补充 schema
        context = self.enhance_with_schema(context)

        logger.info(
            f"检索完成: {len(context.tables)} 个表, "
            f"{len(context.columns)} 个列, "
            f"{len(context.values)} 个值"
        )
        return context

    def enhance_with_schema(self, context: RetrievedContext) -> RetrievedContext:
        """
        根据检索到的值，补充相关的 schema 信息

        确保值所属的表和列也在上下文中
        """
        existing_col_keys = {
            f"{c.table_name}.{c.name}" for c in context.columns
        }
        existing_table_names = {t.name for t in context.tables}

        for val_item in context.values:
            # 确保值所属的表在 tables 中
            if val_item.table_name and val_item.table_name not in existing_table_names:
                context.tables.append(RetrievedItem(
                    item_type="table",
                    name=val_item.table_name,
                    table_name=val_item.table_name,
                    score=val_item.score * 0.8,  # 间接关联，略降权
                    metadata={"source": "value_retrieval"},
                ))
                existing_table_names.add(val_item.table_name)

            # 确保值所属的列在 columns 中
            col_name = val_item.metadata.get("column_name")
            if col_name and val_item.table_name:
                col_key = f"{val_item.table_name}.{col_name}"
                if col_key not in existing_col_keys:
                    context.columns.append(RetrievedItem(
                        item_type="column",
                        name=col_name,
                        table_name=val_item.table_name,
                        score=val_item.score * 0.9,
                        metadata={"source": "value_retrieval"},
                    ))
                    existing_col_keys.add(col_key)

        return context

    # ------------------------------------------------------------------
    # LangGraph 子图接口（依据 决策 22 / §18.3 / §18.8）
    # ------------------------------------------------------------------
    def build_graph(self):
        """
        返回 IR Agent 的已编译 LangGraph 子图

        子图节点：extract_keywords → retrieve_values → retrieve_schema → assemble
        子图输入字段：user_query (必填), database_filter (可选)
        子图输出字段：retrieved_context (RetrievedContext)
        """
        from src.retrieval.ir_graph import build_ir_graph
        return build_ir_graph(self)

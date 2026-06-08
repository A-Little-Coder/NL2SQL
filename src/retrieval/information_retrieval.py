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
from pathlib import Path
from typing import List, Dict, Any, Optional
from loguru import logger


# ============================================================================
# N-gram 工具函数
# ============================================================================

def _char_ngrams(text: str, n: int = 3) -> set:
    """生成文本的字符级 n-gram 集合"""
    if len(text) < n:
        return {text} if text else set()
    return {text[i:i + n] for i in range(len(text) - n + 1)}


def ngram_vote_score(document: str, query_terms: list, n: int = 3) -> float:
    """
    计算 N-gram 投票得分

    只对 query_terms 做 n-gram 拆解，在 document 原文中统计每个 n-gram 的出现次数。
    document 不拆解，避免 '|' 分隔符产生噪声 n-gram。

    计分方式：累加所有 term 的所有 n-gram 在 document 中的出现次数。
    例如 "school" 的 "sch" 在 document 中出现 2 次 → 贡献 2 分。

    Args:
        document: 候选列的 document 文本（全小写，不拆解）
        query_terms: 所有检索词（全小写）
        n: n-gram 的 n 值，默认 3

    Returns:
        float: 所有 n-gram 在 document 中的累计出现次数
    """
    if not document:
        return 0.0

    total_hits = 0
    for term in query_terms:
        term_ngrams = _char_ngrams(term, n)
        for ng in term_ngrams:
            total_hits += document.count(ng)
    return float(total_hits)

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
class KeywordGroup:
    """关键词分组 — 一个原生关键词及其同义词扩写"""
    phrase: str                    # 原生关键词（如"各科score"）
    terms: List[str] = None        # phrase + zh_synonyms + en_synonyms（全小写，去重）

    def __post_init__(self):
        if self.terms is None:
            self.terms = []


@dataclass
class RetrievedContext:
    """检索上下文 - 整合所有检索结果"""
    tables: List[RetrievedItem] = None
    columns: List[RetrievedItem] = None
    values: List[RetrievedItem] = None
    keywords: List[str] = None
    keyword_groups: List[KeywordGroup] = None       # 关键词分组（保留结构化信息）
    keyword_columns_map: Dict[str, List[str]] = None  # 关键词→召回列映射 {"各科score": ["satscores.AvgScrRead", ...]}
    join_paths: List[Dict[str, Any]] = None          # 表关联图的 JOIN 路径（决策 26）
    join_paths_text: str = ""                         # 格式化的 JOIN 条件文本（用于 Prompt 注入）
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
        if self.keyword_groups is None:
            self.keyword_groups = []
        if self.keyword_columns_map is None:
            self.keyword_columns_map = {}
        if self.join_paths is None:
            self.join_paths = []

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

KEYWORD_EXTRACTION_PROMPT = """你是一个专业的数据库查询分析专家。请从用户查询中提取用于数据库检索的关键词，并为每个关键词提供同义词扩写。

提取规则：
1. 提取可能与数据库字段名或值匹配的关键词
2. 名词前的描述性定语、量词不单独切分，保留为短语（如"各科成绩"不拆成"各科"+"成绩"）
3. 时间表达式保留原样（如"去年"、"2023年"）
4. 度量词保留（如"销售额"、"利润"）
5. 实体名称保留（如"苹果"、"北京"）
6. 不要提取常见停用词（如"显示"、"查询"、"找出"等动词）
7. 为每个关键词提供中文同义词和英文同义词/翻译
8. 所有输出全小写
9. 返回 JSON 格式：{{"keywords": [{{"phrase": "关键词", "zh_synonyms": ["同义词1"], "en_synonyms": ["synonym1"]}}]}}

示例：
输入："各个学校的各科score"
输出：{{"keywords": [{{"phrase": "学校", "zh_synonyms": ["学校", "院校"], "en_synonyms": ["school", "schools"]}}, {{"phrase": "各科score", "zh_synonyms": ["各科成绩", "每科分数"], "en_synonyms": ["subject score", "course score", "each subject score"]}}]}}

输入："显示去年北京地区的销售额"
输出：{{"keywords": [{{"phrase": "去年", "zh_synonyms": ["去年"], "en_synonyms": ["last year", "previous year"]}}, {{"phrase": "北京", "zh_synonyms": ["北京", "北京市"], "en_synonyms": ["beijing"]}}, {{"phrase": "销售额", "zh_synonyms": ["销售额", "营收"], "en_synonyms": ["sales", "revenue", "sales amount"]}}]}}

输入："找出销售额超过100万的客户"
输出：{{"keywords": [{{"phrase": "销售额", "zh_synonyms": ["销售额", "营收"], "en_synonyms": ["sales", "revenue", "sales amount"]}}, {{"phrase": "100万", "zh_synonyms": ["100万", "一百万"], "en_synonyms": ["1 million", "1000000"]}}, {{"phrase": "客户", "zh_synonyms": ["客户", "顾客"], "en_synonyms": ["customer", "client"]}}]}}

输入："{query}"
输出："""

# 当提供了会话历史时，当前查询可能是对上一轮的 follow-up（如"那去年的呢"），
# 请结合历史查询来补全省略的关键词。
FOLLOW_UP_INSTRUCTION = """
注意：以上是当前查询之前的会话历史。当前查询可能是对历史查询的 follow-up（省略句）。
请结合历史查询来理解当前查询的完整意图，提取完整的关键词。

示例：
历史：["查询苹果的销售额"]
当前："那去年的呢"
理解：用户想知道"苹果去年的销售额"
关键词：应包含"苹果"、"去年"、"销售额"

历史：["展示各个学校的各科score"]
当前："只看北京的呢"
理解：用户想知道"北京地区各个学校的各科score"
关键词：应包含"北京"、"学校"、"各科score"
"""


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

    def extract_keywords(self, query: str, conversation_history: List[Dict[str, Any]] = None) -> List[KeywordGroup]:
        """
        从自然语言查询中提取关键词（含同义词扩写，按原生关键词分组）

        支持 follow-up 查询：如果提供了会话历史，会在 prompt 中注入上一轮查询
        辅助 LLM 理解"那去年的呢"类省略句。

        Args:
            query: 用户查询
            conversation_history: 可选，当前会话的历史轮次列表

        Returns:
            List[KeywordGroup]: 关键词分组列表，每组含 phrase 和 terms
        """
        if not self.llm_client:
            logger.warning("LLM 客户端未设置，使用简单分词回退")
            simple_kws = self._simple_keyword_extract(query)
            return [KeywordGroup(phrase=kw, terms=[kw.lower()]) for kw in simple_kws]

        try:
            # 注入会话历史（辅助 follow-up 理解）
            history_context = ""
            if conversation_history:
                history_lines = []
                for turn in conversation_history[-3:]:  # 最多最近 3 轮
                    q = turn.get("user_query", "")
                    if q:
                        history_lines.append(f"  - \"{q}\"")
                if history_lines:
                    history_context = "\n\n## 会话历史（当前查询之前的对话）：\n" + "\n".join(history_lines)

            prompt = KEYWORD_EXTRACTION_PROMPT.format(query=query)
            if history_context:
                prompt += history_context + "\n\n" + FOLLOW_UP_INSTRUCTION

            messages = [
                {"role": "system", "content": "你是数据库查询分析专家，只输出 JSON。"},
                {"role": "user", "content": prompt},
            ]
            result = self.llm_client.chat_json(messages, temperature=0.0)
            keywords_raw = result.get("keywords", [])

            groups = []
            for kw in keywords_raw:
                if isinstance(kw, str):
                    # 兼容旧格式
                    groups.append(KeywordGroup(phrase=kw, terms=[kw.lower()]))
                elif isinstance(kw, dict):
                    phrase = kw.get("phrase", "")
                    if not phrase:
                        continue
                    # 扁平化：phrase + zh_synonyms + en_synonyms，全小写，去重保序
                    terms = []
                    seen = set()
                    for t in [phrase] + kw.get("zh_synonyms", []) + kw.get("en_synonyms", []):
                        t_lower = t.lower()
                        if t_lower not in seen:
                            seen.add(t_lower)
                            terms.append(t_lower)
                    groups.append(KeywordGroup(phrase=phrase.lower(), terms=terms))

            logger.info(f"关键词提取结果: {[(g.phrase, g.terms) for g in groups]}")
            return groups

        except Exception as e:
            logger.warning(f"LLM 关键词提取失败，使用回退方案: {e}")
            simple_kws = self._simple_keyword_extract(query)
            return [KeywordGroup(phrase=kw, terms=[kw.lower()]) for kw in simple_kws]

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

    def retrieve_schema(self, keyword_groups: List[KeywordGroup], database_filter: str = None,
                      column_top_k_per_keyword: int = 50) -> Dict[str, List[RetrievedItem]]:
        """
        按关键词分组独立召回 schema，每组组内 N-gram 投票精排

        流程：
        1. 每个关键词组：组内所有 terms 各查 top50 → 取并集
        2. 组内 N-gram 投票精排（只用本组 terms）
        3. 每组返回 top 5 列

        Args:
            keyword_groups: 关键词分组列表
            database_filter: 可选的数据库过滤条件
            column_top_k_per_keyword: 每个 term 查 top_k 个列（默认 50）

        Returns:
            Dict[str, List[RetrievedItem]]: key 为原生关键词 phrase，value 为该组的 top5 列
        """
        if not self.vector_store:
            logger.warning("向量存储未设置，跳过 schema 检索")
            return {}

        if not hasattr(self, "_vectorizer") or self._vectorizer is None:
            logger.warning("向量化器未设置，跳过 schema 检索")
            return {}

        if not keyword_groups:
            return {}

        where_filter = None
        if database_filter:
            where_filter = {"database": database_filter}

        group_results = {}

        try:
            for group in keyword_groups:
                if not group.terms:
                    continue

                # 1. 组内所有 terms 各自查 top50，取并集（不去重，全部保留）
                group_raw_results = []
                for term in group.terms:
                    try:
                        embedding_result = self._vectorizer.embed_texts([term], return_dense=True)
                        query_vec = embedding_result["dense"][0]

                        results_for_term = self.vector_store.query(
                            query_embedding=query_vec,
                            n_results=column_top_k_per_keyword,
                            where_filter=where_filter,
                        )
                        group_raw_results.extend(results_for_term)
                    except Exception as e:
                        logger.warning(f"组 '{group.phrase}' 检索词 '{term}' 失败: {e}")
                        continue

                if not group_raw_results:
                    group_results[group.phrase] = []
                    continue

                # 2. 组内 N-gram 投票精排
                candidates = []
                for r in group_raw_results:
                    meta = r.get("metadata", {})
                    dist = r.get("distance", 1.0)
                    vector_score = 1.0 - dist if dist is not None else 0.0

                    table_name = meta.get("table_name", "")
                    col_name = meta.get("original_column_name", meta.get("column_name", ""))
                    if not table_name or not col_name:
                        continue

                    key = f"{table_name}.{col_name}"
                    document = r.get("document", "")

                    # 组内投票：只用本组的 terms
                    vote = ngram_vote_score(document, group.terms, n=3)

                    candidates.append({
                        "key": key,
                        "metadata": meta,
                        "document": document,
                        "vector_score": vector_score,
                        "ngram_vote": vote,
                    })

                if not candidates:
                    group_results[group.phrase] = []
                    continue

                # 3. 计算综合分并去重（同 key 取最高 final_score）
                max_ngram = max(c["ngram_vote"] for c in candidates)
                max_ngram = max(max_ngram, 1.0)

                dedup_map = {}
                for c in candidates:
                    normalized_ngram = c["ngram_vote"] / max_ngram
                    final_score = c["vector_score"] * 0.2 + normalized_ngram * 0.8

                    if c["key"] not in dedup_map or final_score > dedup_map[c["key"]]["final_score"]:
                        dedup_map[c["key"]] = {
                            "final_score": final_score,
                            "metadata": c["metadata"],
                        }

                # 4. 按 final_score 降序，取 top 10
                sorted_items = sorted(dedup_map.items(), key=lambda x: x[1]["final_score"], reverse=True)
                group_columns = []
                for key, info in sorted_items[:10]:
                    col_name = info["metadata"].get("original_column_name",
                                                     info["metadata"].get("column_name", ""))
                    table_name = info["metadata"].get("table_name", "")
                    group_columns.append(RetrievedItem(
                        item_type="column",
                        name=col_name,
                        table_name=table_name,
                        score=info["final_score"],
                        metadata=info["metadata"],
                    ))

                group_results[group.phrase] = group_columns
                logger.info(
                    f"组 '{group.phrase}': 粗召回 {len(group_raw_results)}, "
                    f"去重 {len(dedup_map)}, top10: {[c.name for c in group_columns]}"
                )

            return group_results

        except Exception as e:
            logger.error(f"语义 schema 检索失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return {}

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

        # 1. 关键词提取（返回分组）
        keyword_groups = self.extract_keywords(query)
        # 扁平化关键词列表（兼容旧接口）
        all_keywords = []
        for g in keyword_groups:
            all_keywords.extend(g.terms)
        logger.info(f"提取关键词: {[(g.phrase, g.terms) for g in keyword_groups]}")

        # 2. LSH 值检索（用扁平化关键词）
        value_items = self.retrieve_values(all_keywords)

        # 3. 语义 schema 检索（按分组独立召回）
        group_schema_results = self.retrieve_schema(keyword_groups, database_filter)

        # 4. 跨组汇总：合并所有列，去重但标注来源
        seen_columns = {}  # key → RetrievedItem
        keyword_columns_map = {}  # phrase → [column_key, ...]

        for phrase, columns in group_schema_results.items():
            col_keys = []
            for col in columns:
                col_key = f"{col.table_name}.{col.name}"
                col_keys.append(col_key)
                if col_key not in seen_columns:
                    # 首次出现，记录
                    seen_columns[col_key] = col
                else:
                    # 重复列：保留分数更高的
                    if col.score > seen_columns[col_key].score:
                        seen_columns[col_key] = col
            keyword_columns_map[phrase] = col_keys

        all_columns = list(seen_columns.values())
        all_columns.sort(key=lambda c: c.score, reverse=True)

        # 提取表信息
        seen_tables = set()
        all_tables = []
        for col in all_columns:
            if col.table_name and col.table_name not in seen_tables:
                seen_tables.add(col.table_name)
                all_tables.append(RetrievedItem(
                    item_type="table",
                    name=col.table_name,
                    table_name=col.table_name,
                    score=col.score,
                    metadata={"database": col.metadata.get("database", "")},
                ))

        # 5. 合并结果
        context = RetrievedContext(
            tables=all_tables,
            columns=all_columns,
            values=value_items,
            keywords=all_keywords,
            keyword_groups=keyword_groups,
            keyword_columns_map=keyword_columns_map,
            lsh_hit_count=len(value_items),
            vector_top_scores=[c.score for c in all_columns[:10]],
        )

        # 6. 根据检索到的值补充 schema
        context = self.enhance_with_schema(context)

        # 7. 注入 JOIN 路径（决策 26）
        context = self._inject_join_paths(context, database_filter)

        logger.info(
            f"检索完成: {len(context.tables)} 个表, "
            f"{len(context.columns)} 个列, "
            f"{len(context.values)} 个值, "
            f"关键词组: {len(keyword_groups)}"
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

    def _inject_join_paths(
        self, context: RetrievedContext, database_filter: str = None
    ) -> RetrievedContext:
        """
        根据 RetrievedContext 中涉及的表，从预构建的表关联图中提取 JOIN 路径

        依据决策 26：IR 召回后，根据召回的表集合从图中提取 JOIN 路径和连接键，注入 Prompt。
        桥接表（路径中出现但未被 IR 召回的表）的 M-Schema 自动补充到 context 中。
        """
        if not database_filter:
            logger.debug("未指定 database_filter，跳过 JOIN 路径注入")
            return context

        table_names = context.get_all_table_names()
        if len(table_names) < 2:
            logger.debug("涉及表不足 2 个，无需 JOIN 路径")
            return context

        try:
            from src.preprocessing.schema_graph_builder import (
                SchemaGraphBuilder,
                extract_join_paths,
                format_join_paths_for_prompt,
            )

            # 加载预构建的图
            graph_dir = Path(__file__).parent.parent.parent / "data" / "preprocessed" / "schema_graphs"
            graph_path = graph_dir / f"{database_filter}.json"

            if not graph_path.exists():
                logger.debug(f"表关联图不存在: {graph_path}")
                return context

            graph = SchemaGraphBuilder.load(str(graph_path))
            join_paths_result = extract_join_paths(graph, table_names)

            edges = join_paths_result.get("edges", [])
            bridge_tables = join_paths_result.get("bridge_tables", [])

            if edges:
                context.join_paths = edges
                context.join_paths_text = format_join_paths_for_prompt(join_paths_result)
                logger.info(f"JOIN 路径注入: {len(edges)} 条关联")

                # 补充桥接表的 M-Schema
                if bridge_tables:
                    self._add_bridge_tables(context, bridge_tables, database_filter)
            else:
                logger.debug("未找到相关 JOIN 路径")

        except Exception as e:
            logger.warning(f"JOIN 路径注入失败: {e}")

        return context

    def _add_bridge_tables(
        self, context: RetrievedContext, bridge_tables: List[str], database_filter: str
    ) -> None:
        """
        将桥接表的列从向量库补充到 RetrievedContext 中

        桥接表未被关键词召回，但 JOIN 路径需要它，LLM 必须知道这些表的 schema 才能写 JOIN。
        """
        if not self.vector_store or not hasattr(self, "_vectorizer") or self._vectorizer is None:
            return

        existing_table_names = {t.name for t in context.tables}
        existing_col_keys = {f"{c.table_name}.{c.name}" for c in context.columns}

        for table_name in bridge_tables:
            if table_name in existing_table_names:
                continue

            # 从向量库查询该表的所有列
            try:
                where_filter = {
                    "$and": [
                        {"database": database_filter},
                        {"table_name": table_name},
                    ]
                }
                # 用一个通用的 query 获取该表的列（用表名做 query）
                embedding_result = self._vectorizer.embed_texts([table_name], return_dense=True)
                query_vec = embedding_result["dense"][0]
                results = self.vector_store.query(
                    query_embedding=query_vec,
                    n_results=50,  # 取足够多确保覆盖该表所有列
                    where_filter=where_filter,
                )

                # 添加桥接表
                context.tables.append(RetrievedItem(
                    item_type="table",
                    name=table_name,
                    table_name=table_name,
                    score=0.0,
                    metadata={"database": database_filter, "source": "bridge_table"},
                ))
                existing_table_names.add(table_name)

                # 添加桥接表的列
                for r in results:
                    meta = r.get("metadata", {})
                    col_name = meta.get("original_column_name", meta.get("column_name", ""))
                    t_name = meta.get("table_name", "")
                    if t_name != table_name or not col_name:
                        continue
                    col_key = f"{t_name}.{col_name}"
                    if col_key in existing_col_keys:
                        continue
                    context.columns.append(RetrievedItem(
                        item_type="column",
                        name=col_name,
                        table_name=t_name,
                        score=0.0,
                        metadata={**meta, "source": "bridge_table"},
                    ))
                    existing_col_keys.add(col_key)

                logger.info(f"桥接表补充: {table_name}")

            except Exception as e:
                logger.warning(f"桥接表 {table_name} 补充失败: {e}")

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

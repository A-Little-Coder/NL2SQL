# ============================================================================
# Schema 关联图构建器（Schema Relationship Graph Builder）
# ============================================================================
# 功能说明:
#   为每个数据库构建表关联图（JSON 邻接表），包含：
#   - Stage 1: 显式 FK（PRAGMA foreign_key_list）
#   - Stage 2: 向量相似度匹配 + 值重叠验证
#   - Stage 3: LLM 辅助（覆盖死角）
#
# 图结构存储为 JSON 邻接表，运行时通过 BFS 提取 JOIN 路径。
#
# 用法:
#   builder = SchemaGraphBuilder(db_path, vector_store, llm_client=llm)
#   graph = builder.build(db_id="california_schools")
#   builder.save(graph, "data/preprocessed/schema_graphs/california_schools.json")
# ============================================================================


import json
import os
from collections import defaultdict, deque
from itertools import combinations
from typing import Any, Dict, List, Optional, Set, Tuple

from loguru import logger


class SchemaGraphBuilder:
    """
    表关联图构建器

    为指定数据库构建 JSON 邻接表，记录表间的 JOIN 关系和连接键。

    Attributes:
        db_connector: 数据库连接器
        vector_store: 向量存储管理器（ChromaDB）
        llm_client: LLM 客户端（可选，用于 Stage 3）
        hit_rate_threshold: 值命中率阈值（默认 0.5）
        top_similar_pairs: 每对表取向量最相似的 top N 列对
        sample_size: 命中率检测时采样数量
    """

    def __init__(
        self,
        db_connector,
        vector_store=None,
        llm_client=None,
        hit_rate_threshold: float = 0.5,
        top_similar_pairs: int = 3,
        sample_size: int = 20,
    ):
        self.db_connector = db_connector
        self.vector_store = vector_store
        self.llm_client = llm_client
        self.hit_rate_threshold = hit_rate_threshold
        self.top_similar_pairs = top_similar_pairs
        self.sample_size = sample_size

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def build(self, db_id: str) -> dict:
        """
        构建指定数据库的表关联图

        Args:
            db_id: 数据库标识（如 "california_schools"）

        Returns:
            dict: 图结构，包含 nodes 和 edges
        """
        logger.info(f"开始构建表关联图: {db_id}")

        # 获取所有表及其 schema
        all_schemas = self.db_connector.get_all_schemas()
        table_names = list(all_schemas.keys())
        logger.info(f"  共 {len(table_names)} 个表")

        # 构建 nodes
        nodes = {}
        for table_name, schema in all_schemas.items():
            nodes[table_name] = {
                "columns": [col["name"] for col in schema.get("columns", [])]
            }

        # Stage 1: 显式 FK
        edges, connected_pairs = self._stage1_explicit_fk(all_schemas)
        logger.info(f"  Stage 1 (显式 FK): {len(edges)} 条边")

        # Stage 2: 向量相似度匹配 + 值重叠验证
        stage2_edges = self._stage2_vector_similarity(
            db_id, table_names, all_schemas, connected_pairs
        )
        edges.extend(stage2_edges)
        for e in stage2_edges:
            connected_pairs.add(_pair_key(e["from"], e["to"]))
        logger.info(f"  Stage 2 (向量匹配): {len(stage2_edges)} 条边")

        # Stage 3: LLM 辅助
        stage3_edges = self._stage3_llm_auxiliary(
            db_id, table_names, all_schemas, connected_pairs
        )
        edges.extend(stage3_edges)
        logger.info(f"  Stage 3 (LLM 辅助): {len(stage3_edges)} 条边")

        graph = {
            db_id: {
                "nodes": nodes,
                "edges": edges,
            }
        }

        logger.info(f"表关联图构建完成: {len(edges)} 条边, {len(nodes)} 个节点")
        return graph

    # ------------------------------------------------------------------
    # Stage 1: 显式 FK
    # ------------------------------------------------------------------

    def _stage1_explicit_fk(
        self, all_schemas: dict
    ) -> Tuple[List[dict], Set[str]]:
        """
        从 PRAGMA foreign_key_list 提取显式外键关系

        Returns:
            (edges, connected_pairs): 边列表 + 已连接的表对集合
        """
        edges = []
        connected_pairs = set()

        for table_name, schema in all_schemas.items():
            for fk in schema.get("foreign_keys", []):
                from_col = fk["column"]
                to_table = fk["references_table"]
                to_col = fk["references_column"]

                if to_table not in all_schemas:
                    continue

                pair = _pair_key(table_name, to_table)
                connected_pairs.add(pair)

                # 检查是否已有此边的记录（可能有多个 FK 指向同一表）
                existing = _find_edge(edges, table_name, to_table)
                if existing:
                    existing["join_keys"].append(
                        [f"{table_name}.{from_col}", f"{to_table}.{to_col}"]
                    )
                else:
                    edges.append({
                        "from": table_name,
                        "to": to_table,
                        "join_keys": [
                            [f"{table_name}.{from_col}", f"{to_table}.{to_col}"]
                        ],
                        "type": "explicit_fk",
                    })

        return edges, connected_pairs

    # ------------------------------------------------------------------
    # Stage 2: 向量相似度匹配 + 值重叠验证
    # ------------------------------------------------------------------

    def _stage2_vector_similarity(
        self,
        db_id: str,
        table_names: List[str],
        all_schemas: dict,
        connected_pairs: Set[str],
    ) -> List[dict]:
        """
        对未被 Stage 1 连接的表对，用向量库做列间相似度匹配，
        取 top N 列对，再通过值重叠验证确认 join_keys

        流程：
        1. 从向量库获取每个表的所有列及其 embedding
        2. 对每对未连接的表，计算跨表列的向量相似度
        3. 取最相似的 top N 列对
        4. 对候选列对做值重叠检测（20 样本，Jaccard）
        5. 通过的列对作为 join_key
        """
        if not self.vector_store:
            logger.warning("向量存储未设置，跳过 Stage 2")
            return []

        # 1. 从向量库获取每个表的所有列条目
        table_columns = self._load_table_columns_from_vector_store(db_id)
        if not table_columns:
            logger.warning(f"向量库中未找到 {db_id} 的列数据，跳过 Stage 2")
            return []

        edges = []

        # 2. 对每对未连接的表
        for table_a, table_b in combinations(table_names, 2):
            pair = _pair_key(table_a, table_b)
            if pair in connected_pairs:
                continue

            cols_a = table_columns.get(table_a, [])
            cols_b = table_columns.get(table_b, [])
            if not cols_a or not cols_b:
                continue

            # 3. 用向量库做跨表列相似度匹配
            similar_pairs = self._find_similar_column_pairs(
                db_id, table_a, table_b, cols_a, cols_b
            )

            if not similar_pairs:
                continue

            # 4. 对 top N 候选做值重叠验证
            verified_keys = self._verify_value_overlap(
                similar_pairs[: self.top_similar_pairs],
                all_schemas,
            )

            if verified_keys:
                edges.append({
                    "from": table_a,
                    "to": table_b,
                    "join_keys": verified_keys,
                    "type": "vector_similarity",
                })

        return edges

    def _load_table_columns_from_vector_store(
        self, db_id: str
    ) -> Dict[str, List[dict]]:
        """
        从向量库获取指定数据库的所有列条目

        Returns:
            {table_name: [{"id", "metadata", "document", "embedding"}, ...]}
        """
        try:
            # 使用 ChromaDB 的 get 方法批量获取
            results = self.vector_store.collection.get(
                where={"database": db_id},
                include=["metadatas", "documents", "embeddings"],
            )
        except Exception as e:
            logger.warning(f"从向量库获取列数据失败: {e}")
            return {}

        table_columns = defaultdict(list)
        if not results or not results["ids"]:
            return {}

        for i in range(len(results["ids"])):
            meta = results["metadatas"][i] if results["metadatas"] else {}
            table_name = meta.get("table_name", "")
            if not table_name:
                continue

            embeddings_data = results.get("embeddings")
            embedding = embeddings_data[i] if embeddings_data is not None else None

            table_columns[table_name].append({
                "id": results["ids"][i],
                "metadata": meta,
                "document": results["documents"][i] if results["documents"] else "",
                "embedding": embedding,
            })

        return dict(table_columns)

    def _find_similar_column_pairs(
        self,
        db_id: str,
        table_a: str,
        table_b: str,
        cols_a: List[dict],
        cols_b: List[dict],
    ) -> List[Tuple[dict, dict, float]]:
        """
        用向量库做跨表列相似度匹配

        策略：对表 A 的每个列，用其 embedding 在向量库中查询表 B 的所有列，
        取余弦距离最小的列对

        Returns:
            [(col_a_info, col_b_info, similarity_score), ...] 按 score 降序
        """
        # 构建表 B 列的 embedding 矩阵
        b_embeddings = []
        b_infos = []
        for col in cols_b:
            emb = col.get("embedding")
            if emb is not None:
                b_embeddings.append(emb)
                b_infos.append(col)

        if not b_embeddings:
            return []

        # 对表 A 的每个列，计算与表 B 所有列的余弦相似度
        pair_scores = []
        for col in cols_a:
            emb_a = col.get("embedding")
            if emb_a is None:
                continue

            for j, emb_b in enumerate(b_embeddings):
                sim = _cosine_similarity(emb_a, emb_b)
                pair_scores.append((col, b_infos[j], sim))

        # 按 similarity 降序排序
        pair_scores.sort(key=lambda x: x[2], reverse=True)
        return pair_scores

    def _verify_value_overlap(
        self,
        similar_pairs: List[Tuple[dict, dict, float]],
        all_schemas: dict,
    ) -> List[List[str]]:
        """
        对候选列对做值命中率检测

        从表 A 的列取 N 个 DISTINCT 值，检查这些值在表 B 的列中有多少能匹配到。
        命中率 = 匹配数 / 样本数，超过阈值则确认为 join_key。

        比起 Jaccard（要求双方取样有交集），命中率对大表更可靠：
        - 大表双方各取 20 个值，Jaccard 交集概率极低
        - 命中率只需验证"表 A 的值在表 B 中是否存在"，即使表 B 有百万行也准确

        Returns:
            [[from_table.from_col, to_table.to_col], ...] 通过验证的 join_key 列表
        """
        verified_keys = []

        for col_a, col_b, sim_score in similar_pairs:
            meta_a = col_a.get("metadata", {})
            meta_b = col_b.get("metadata", {})

            table_a = meta_a.get("table_name", "")
            table_b = meta_b.get("table_name", "")
            col_name_a = meta_a.get("original_column_name", meta_a.get("column_name", ""))
            col_name_b = meta_b.get("original_column_name", meta_b.get("column_name", ""))

            if not table_a or not table_b or not col_name_a or not col_name_b:
                continue

            # 跳过类型不兼容的列对（如一个是 TEXT，一个是 INTEGER）
            dtype_a = meta_a.get("data_type", "").upper()
            dtype_b = meta_b.get("data_type", "").upper()
            if dtype_a and dtype_b and not _types_compatible(dtype_a, dtype_b):
                continue

            # 从表 A 取样本值
            values_a = self._get_column_samples(table_a, col_name_a)
            if not values_a:
                continue

            # 过滤掉 None 值，转小写
            sample_values = [str(v).lower().strip() for v in values_a if v is not None]
            sample_values = [v for v in sample_values if v]  # 去空字符串
            if not sample_values:
                continue

            # 检查表 A 的样本值在表 B 的列中能匹配多少
            hit_count = self._check_hit_rate(table_b, col_name_b, sample_values)

            hit_rate = hit_count / len(sample_values) if sample_values else 0.0

            if hit_rate >= self.hit_rate_threshold:
                verified_keys.append([
                    f"{table_a}.{col_name_a}",
                    f"{table_b}.{col_name_b}",
                ])
                logger.debug(
                    f"  命中率验证通过: {table_a}.{col_name_a} ↔ {table_b}.{col_name_b} "
                    f"(hit_rate={hit_rate:.3f}={hit_count}/{len(sample_values)}, 向量相似度={sim_score:.3f})"
                )

        return verified_keys

    def _get_column_samples(
        self, table_name: str, col_name: str
    ) -> List[Any]:
        """从数据库中获取某列的 DISTINCT 样本值"""
        try:
            sql = f'SELECT DISTINCT "{col_name}" FROM "{table_name}" LIMIT {self.sample_size}'
            success, result, error = self.db_connector.execute_query(sql)
            if success and result:
                return [row[0] for row in result]
        except Exception as e:
            logger.debug(f"获取样本值失败 {table_name}.{col_name}: {e}")
        return []

    def _check_hit_rate(
        self, table_name: str, col_name: str, sample_values: List[str]
    ) -> int:
        """
        检查样本值在目标表的指定列中有多少能匹配到

        使用 SQL IN 子句查询，避免将大量数据拉到内存。

        Args:
            table_name: 目标表名
            col_name: 目标列名
            sample_values: 待检查的值列表（已小写）

        Returns:
            int: 匹配到的数量
        """
        if not sample_values:
            return 0

        try:
            # 用 SQL IN 查询目标列中是否存在这些值
            # 分批查询避免 SQL 过长（每批最多 50 个值）
            batch_size = 50
            matched_values = set()

            for i in range(0, len(sample_values), batch_size):
                batch = sample_values[i:i + batch_size]
                # 转义单引号防止 SQL 注入
                escaped = [v.replace("'", "''") for v in batch]
                values_str = ",".join(f"'{v}'" for v in escaped)

                sql = (
                    f'SELECT DISTINCT LOWER(CAST("{col_name}" AS TEXT)) '
                    f'FROM "{table_name}" '
                    f'WHERE LOWER(CAST("{col_name}" AS TEXT)) IN ({values_str})'
                )
                success, result, error = self.db_connector.execute_query(sql)
                if success and result:
                    for row in result:
                        matched_values.add(str(row[0]).lower().strip())

            # 统计样本值中有多少被匹配到
            hit_count = sum(1 for v in sample_values if v in matched_values)
            return hit_count

        except Exception as e:
            logger.debug(f"命中率检测失败 {table_name}.{col_name}: {e}")
            return 0

    # ------------------------------------------------------------------
    # Stage 3: LLM 辅助
    # ------------------------------------------------------------------

    def _stage3_llm_auxiliary(
        self,
        db_id: str,
        table_names: List[str],
        all_schemas: dict,
        connected_pairs: Set[str],
    ) -> List[dict]:
        """
        对 Stage 1-2 都没发现关联的孤立表对，用 LLM 判断是否可能 JOIN

        Returns:
            新发现的边列表
        """
        if not self.llm_client:
            logger.info("  LLM 客户端未设置，跳过 Stage 3")
            return []

        # 找出所有未连接的表对
        isolated_pairs = []
        for table_a, table_b in combinations(table_names, 2):
            if _pair_key(table_a, table_b) not in connected_pairs:
                isolated_pairs.append((table_a, table_b))

        if not isolated_pairs:
            return []

        logger.info(f"  Stage 3: 检查 {len(isolated_pairs)} 对孤立表")

        edges = []
        for table_a, table_b in isolated_pairs:
            join_keys = self._llm_infer_join(
                db_id, table_a, table_b, all_schemas
            )
            if join_keys:
                # LLM 推断的 join_keys 也需要经过命中率检测验证
                verified_keys = self._verify_llm_join_keys(
                    table_a, table_b, join_keys
                )
                if verified_keys:
                    edges.append({
                        "from": table_a,
                        "to": table_b,
                        "join_keys": verified_keys,
                        "type": "llm_inferred",
                    })

        return edges

    def _llm_infer_join(
        self, db_id: str, table_a: str, table_b: str, all_schemas: dict
    ) -> List[List[str]]:
        """
        用 LLM 推断两个表之间是否存在 JOIN 关系

        Returns:
            [[from_table.from_col, to_table.to_col], ...] 或空列表
        """
        schema_a = all_schemas.get(table_a, {})
        schema_b = all_schemas.get(table_b, {})

        # 构建 schema 描述
        desc_a = self._format_schema_for_llm(table_a, schema_a)
        desc_b = self._format_schema_for_llm(table_b, schema_b)

        from src.preprocessing.prompts import JOIN_INFER_PROMPT
        messages = JOIN_INFER_PROMPT.format_messages(
            desc_a=desc_a,
            desc_b=desc_b,
            table_a=table_a,
            table_b=table_b,
        )

        try:
            # 离线脚本场景：用 invoke 一步到位（无 SSE 上下文）
            result = self.llm_client.invoke(messages, as_json=True, temperature=0.0, thinking=False, run_name="join-inference")

            if result.get("has_join") and result.get("join_keys"):
                logger.debug(
                    f"  LLM 推断: {table_a} ↔ {table_b} "
                    f"join_keys={result['join_keys']}"
                )
                return result["join_keys"]

        except Exception as e:
            logger.debug(f"  LLM 推断失败 {table_a} ↔ {table_b}: {e}")

        return []

    def _format_schema_for_llm(self, table_name: str, schema: dict) -> str:
        """格式化表的 schema 供 LLM 分析"""
        lines = [f"表名: {table_name}"]
        lines.append("列:")
        for col in schema.get("columns", []):
            col_info = f"  - {col['name']} ({col.get('type', 'UNKNOWN')})"
            if col.get("description"):
                col_info += f" — {col['description']}"
            lines.append(col_info)

        # 添加样本值
        sample_values = schema.get("sample_values", {})
        if sample_values:
            lines.append("样本值:")
            for col_name, samples in sample_values.items():
                if samples:
                    lines.append(f"  - {col_name}: {samples[:5]}")

        return "\n".join(lines)

    def _verify_llm_join_keys(
        self, table_a: str, table_b: str, join_keys: List[List[str]]
    ) -> List[List[str]]:
        """对 LLM 推断的 join_keys 进行命中率检测验证

        对每对 (col_a_full, col_b_full)，从 table_a 采样 sample_size 个值，
        检查在 table_b 中命中多少，过滤掉低命中率的"幻觉"关联。
        """
        verified = []
        for jk in join_keys:
            if len(jk) != 2:
                continue
            col_a_full, col_b_full = jk[0], jk[1]
            col_a_name = col_a_full.split(".", 1)[1] if "." in col_a_full else col_a_full
            col_b_name = col_b_full.split(".", 1)[1] if "." in col_b_full else col_b_full
            if not col_a_name or not col_b_name:
                continue

            values_a = self._get_column_samples(table_a, col_a_name)
            if not values_a:
                continue

            sample_values = [str(v).lower().strip() for v in values_a if v is not None]
            sample_values = [v for v in sample_values if v]
            if not sample_values:
                continue

            hit_count = self._check_hit_rate(table_b, col_b_name, sample_values)
            hit_rate = hit_count / len(sample_values) if sample_values else 0.0

            if hit_rate >= self.hit_rate_threshold:
                verified.append(jk)
                logger.debug(
                    f"LLM join_key hit rate verified: {col_a_full} ↔ {col_b_full} "
                    f"(hit_rate={hit_rate:.3f}={hit_count}/{len(sample_values)})"
                )
            else:
                logger.debug(
                    f"LLM join_key hit rate failed (hallucination filtered): "
                    f"{col_a_full} ↔ {col_b_full} (hit_rate={hit_rate:.3f})"
                )
        return verified

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    @staticmethod
    def save(graph: dict, output_path: str) -> None:
        """保存图结构为 JSON 文件"""
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(graph, f, ensure_ascii=False, indent=2)
        logger.info(f"表关联图已保存: {output_path}")

    @staticmethod
    def load(path: str) -> dict:
        """从 JSON 文件加载图结构"""
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)


# ============================================================================
# 模块级工具函数
# ============================================================================

def _pair_key(table_a: str, table_b: str) -> str:
    """生成无序表对 key，用于去重"""
    return f"{min(table_a, table_b)}|{max(table_a, table_b)}"


def _find_edge(edges: List[dict], from_table: str, to_table: str) -> Optional[dict]:
    """在边列表中查找指定的表对边"""
    pair = _pair_key(from_table, to_table)
    for edge in edges:
        if _pair_key(edge["from"], edge["to"]) == pair:
            return edge
    return None


def _cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
    """计算两个向量的余弦相似度"""
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = sum(a * a for a in vec_a) ** 0.5
    norm_b = sum(b * b for b in vec_b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _types_compatible(dtype_a: str, dtype_b: str) -> bool:
    """判断两个 SQL 类型是否兼容（可 JOIN）"""
    # 归一化类型
    def normalize(dtype: str) -> str:
        dtype = dtype.upper().strip()
        for base in ["INT", "TEXT", "REAL", "FLOAT", "DOUBLE", "CHAR", "VARCHAR", "NUMERIC", "DECIMAL", "DATE", "DATETIME", "TIMESTAMP", "BOOLEAN", "BLOB"]:
            if base in dtype:
                return base
        return dtype

    norm_a = normalize(dtype_a)
    norm_b = normalize(dtype_b)

    # 数值类型之间兼容
    numeric_types = {"INT", "REAL", "FLOAT", "DOUBLE", "NUMERIC", "DECIMAL"}
    if norm_a in numeric_types and norm_b in numeric_types:
        return True

    # 字符串类型之间兼容
    text_types = {"TEXT", "CHAR", "VARCHAR"}
    if norm_a in text_types and norm_b in text_types:
        return True

    # 同类型兼容
    if norm_a == norm_b:
        return True

    return False


# ============================================================================
# 运行时 JOIN 路径提取
# ============================================================================

def extract_join_paths(
    graph: dict, table_names: List[str]
) -> dict:
    """
    从图中提取指定表集合之间的 JOIN 路径（最短路径优先）

    使用 BFS 在图上查找两两之间的最短路径，提取路径上的边和 join_keys。
    同时识别桥接表（路径中出现但不在 table_names 中的表）。

    Args:
        graph: 图结构 dict（SchemaGraphBuilder.build() 的输出）
        table_names: 需要关联的表名列表

    Returns:
        {
            "edges": [{"from", "to", "join_keys", "type"}, ...],  # 去重后的边列表
            "bridge_tables": [str, ...],  # 桥接表名列表
        }
    """
    if not graph or not table_names:
        return {"edges": [], "bridge_tables": []}

    # 获取图的内部数据（无论 key 是什么）
    graph_data = list(graph.values())[0]
    edges = graph_data.get("edges", [])

    if len(table_names) <= 1:
        return {"edges": [], "bridge_tables": []}

    table_set = set(table_names)

    # 构建邻接表
    adjacency = defaultdict(list)  # table -> [(neighbor, edge_info)]
    for edge in edges:
        from_t = edge["from"]
        to_t = edge["to"]
        edge_info = {
            "from": from_t,
            "to": to_t,
            "join_keys": edge["join_keys"],
            "type": edge["type"],
        }
        adjacency[from_t].append((to_t, edge_info))
        adjacency[to_t].append((from_t, edge_info))

    # BFS 找两两最短路径
    visited_edges = {}  # pair_key -> edge_info
    path_tables = set()  # 路径上出现的所有表

    for source in table_names:
        # BFS from source，找最短路径
        queue = deque([(source, [])])  # (current_node, path_edges)
        seen = {source}

        while queue:
            current, path = queue.popleft()

            for neighbor, edge_info in adjacency[current]:
                if neighbor in seen:
                    continue
                seen.add(neighbor)

                new_path = path + [edge_info]

                # 如果到达了目标表集合中的另一个表
                if neighbor in table_set and neighbor != source:
                    pair = _pair_key(source, neighbor)
                    if pair not in visited_edges:
                        # 记录路径上的所有边和经过的表
                        for e in new_path:
                            p = _pair_key(e["from"], e["to"])
                            if p not in visited_edges:
                                visited_edges[p] = e
                            path_tables.add(e["from"])
                            path_tables.add(e["to"])

                queue.append((neighbor, new_path))

    # 桥接表 = 路径上的表 - 用户请求的表
    bridge_tables = sorted(path_tables - table_set)

    return {
        "edges": list(visited_edges.values()),
        "bridge_tables": bridge_tables,
    }


def format_join_paths_for_prompt(join_paths_result: dict) -> str:
    """
    将 JOIN 路径格式化为 Prompt 文本

    Args:
        join_paths_result: extract_join_paths() 的输出

    Returns:
        格式化的 Prompt 文本
    """
    if not join_paths_result:
        return ""

    edges = join_paths_result.get("edges", [])
    bridge_tables = join_paths_result.get("bridge_tables", [])

    if not edges:
        return ""

    lines = ["表关联:"]

    for edge in edges:
        from_t = edge["from"]
        to_t = edge["to"]
        join_keys = edge["join_keys"]

        # 格式化 join_keys
        key_parts = []
        for jk in join_keys:
            key_parts.append(f"{jk[0]} = {jk[1]}")
        keys_str = ", ".join(key_parts)

        lines.append(f"  {from_t} ←[{keys_str}]→ {to_t}")

    lines.append("")
    lines.append("JOIN 条件:")

    for edge in edges:
        from_t = edge["from"]
        to_t = edge["to"]
        join_keys = edge["join_keys"]

        on_parts = [f"{jk[0]} = {jk[1]}" for jk in join_keys]
        on_str = " AND ".join(on_parts)

        lines.append(f"  {from_t} JOIN {to_t} ON {on_str}")

    if bridge_tables:
        lines.append("")
        lines.append(f"桥接表: {', '.join(bridge_tables)}")

    return "\n".join(lines)

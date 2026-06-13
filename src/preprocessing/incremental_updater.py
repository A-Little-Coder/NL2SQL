# ============================================================================
# 预处理增量更新器（Incremental Updater）
# ============================================================================
# 统一增量更新入口，按依赖顺序执行：
#   ① Schema Index (ChromaDB)
#   ② Schema Graph (JSON)
#   ③ LSH Index (Pickle)
#
# 依赖 Manifest 快照对比来确定 diff 范围。
# ============================================================================


import os
import pickle
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# 确保从项目根目录导入
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from loguru import logger

from src.preprocessing.manifest import (
    DiffResult,
    Manifest,
    TableDiff,
)
from src.preprocessing.vector_store import VectorStoreManager
from src.preprocessing.schema_graph_builder import SchemaGraphBuilder
from src.preprocessing.lsh_index import LSHIndexer
from src.preprocessing.database_connector import DatabaseConnector


# ============================================================================
# 报告数据结构
# ============================================================================


@dataclass
class ModuleReport:
    """单模块增量更新报告"""
    status: str = "skipped"  # "updated" | "skipped" | "failed"
    details: str = ""


@dataclass
class UpdateReport:
    """单库增量更新报告"""
    db_id: str = ""
    diff: DiffResult = field(default_factory=DiffResult)
    schema_index: ModuleReport = field(default_factory=ModuleReport)
    schema_graph: ModuleReport = field(default_factory=ModuleReport)
    lsh_index: ModuleReport = field(default_factory=ModuleReport)

    @property
    def success(self) -> bool:
        return all(
            r.status != "failed"
            for r in [self.schema_index, self.schema_graph, self.lsh_index]
        )


# ============================================================================
# 增量更新器
# ============================================================================


class IncrementalUpdater:
    """
    预处理增量更新器

    按依赖顺序执行三个预处理模块的增量更新：
    1. Schema Index (ChromaDB) — upsert/delete 列向量
    2. Schema Graph (JSON) — 增删节点/边
    3. LSH Index (Pickle) — 表级重建

    使用方式:
        updater = IncrementalUpdater(data_dir="data")
        report = updater.update(db_id="california_schools")
        reports = updater.update_all()
    """

    def __init__(
        self,
        data_dir: str = None,
        manifest_path: str = None,
        skip_llm: bool = False,
        hit_rate_threshold: float = 0.5,
        top_similar_pairs: int = 3,
        sample_size: int = 20,
        llm_client=None,
        bge_model_path: str = None,
    ):
        """
        Args:
            data_dir: 数据目录（默认项目根目录下的 data/）
            manifest_path: Manifest 文件路径
            skip_llm: 是否跳过 Stage 3（LLM 辅助）
            hit_rate_threshold: 值命中率阈值
            top_similar_pairs: 每对表取向量最相似的 top N 列对
            sample_size: 命中率检测采样数量
            llm_client: LLM 客户端
            bge_model_path: BGE-M3 模型路径
        """
        if data_dir is None:
            data_dir = str(Path(__file__).parent.parent.parent / "data")
        self.data_dir = data_dir
        self.skip_llm = skip_llm
        self.hit_rate_threshold = hit_rate_threshold
        self.top_similar_pairs = top_similar_pairs
        self.sample_size = sample_size
        self.llm_client = llm_client
        self.bge_model_path = bge_model_path or os.getenv("BGE_M3_MODEL_PATH", "BAAI/bge-m3")

        self.manifest = Manifest(
            manifest_path=manifest_path or str(Path(data_dir) / "manifest.json")
        )
        self.persist_dir = str(Path(data_dir) / "preprocessed" / "chroma")

    # ======================================================================
    # 统一入口
    # ======================================================================

    def update(self, db_id: str) -> UpdateReport:
        """
        对单个库执行增量更新

        Args:
            db_id: 数据库 ID

        Returns:
            UpdateReport: 更新报告
        """
        report = UpdateReport(db_id=db_id)

        # 1. 读取当前 DB schema
        current_schemas = self._get_current_schemas(db_id)
        if not current_schemas:
            logger.warning(f"无法读取数据库 schema: {db_id}")
            return report

        # 2. 加载 Manifest 并计算 diff
        manifest_data = self.manifest.load()
        old_entry = manifest_data.databases.get(db_id)
        diff = Manifest.compute_diff(old_entry, current_schemas)

        report.diff = diff

        # 3. 按依赖顺序执行增量更新
        # ① Schema Index
        schema_index_needs_full = (
            old_entry is None or old_entry.schema_index_build_time is None
        )
        if schema_index_needs_full and not diff.has_changes:
            logger.info(f"{db_id}: Schema Index 尚未构建，但无 schema 变更（首次需手动全量构建）")
        report.schema_index = self._update_schema_index(
            db_id, diff, current_schemas,
            needs_full=schema_index_needs_full,
        )
        schema_index_changed = report.schema_index.status == "updated"
        if report.schema_index.status == "failed":
            logger.error(f"{db_id}: Schema Index 增量更新失败，停止后续操作")
            return report

        # 更新 Schema Index 的 build_time
        if report.schema_index.status == "updated":
            self.manifest.update_build_time(db_id, "schema_index")

        # ② Schema Graph（依赖 Schema Index）
        if old_entry is None or old_entry.schema_index_build_time is None:
            logger.warning(f"{db_id}: Schema Index 尚未构建，跳过 Schema Graph")
            report.schema_graph = ModuleReport(
                status="skipped", details="Schema Index 未构建"
            )
        else:
            schema_graph_needs_full = (
                old_entry is None or old_entry.schema_graph_build_time is None
            )
            # 级联触发：Schema Index 有变更 → Schema Graph 需要重新处理
            cascade = schema_index_changed
            report.schema_graph = self._update_schema_graph(
                db_id, diff, current_schemas,
                needs_full=schema_graph_needs_full,
                cascade=cascade,
            )
            if report.schema_graph.status == "failed":
                logger.error(f"{db_id}: Schema Graph 增量更新失败，停止后续操作")
                return report

            if report.schema_graph.status == "updated":
                self.manifest.update_build_time(db_id, "schema_graph")

        # ③ LSH Index（独立）
        lsh_needs_full = (
            old_entry is None or old_entry.lsh_index_build_time is None
        )
        report.lsh_index = self._update_lsh_index(
            db_id, diff, needs_full=lsh_needs_full,
        )
        if report.lsh_index.status == "failed":
            logger.error(f"{db_id}: LSH Index 增量更新失败")
            return report

        if report.lsh_index.status == "updated":
            self.manifest.update_build_time(db_id, "lsh_index")

        logger.success(f"{db_id}: 增量更新完成")
        return report

    def update_all(self) -> List[UpdateReport]:
        """扫描所有已记录的库，只更新有 diff 的库"""
        from src.preprocessing.build_schema_index import find_bird_databases

        db_map = find_bird_databases(self.data_dir)
        reports = []

        for db_id in sorted(db_map.keys()):
            report = self.update(db_id)
            reports.append(report)

        return reports

    # ======================================================================
    # ① Schema Index 增量更新
    # ======================================================================

    def _update_schema_index(
        self,
        db_id: str,
        diff: DiffResult,
        current_schemas: dict,
        needs_full: bool = False,
    ) -> ModuleReport:
        """Schema Index (ChromaDB) 增量更新"""
        if needs_full:
            logger.info(f"{db_id}: Schema Index 尚未构建，需要全量构建")
            # 全量构建由外部构建脚本负责，此处返回 skipped
            return ModuleReport(status="skipped", details="未构建，请先运行 build_schema_index")

        if not (diff.added_tables or diff.removed_tables
                or any(t.has_changes for t in diff.modified_tables.values())):
            return ModuleReport(status="skipped", details="无变更")

        try:
            vector_store = VectorStoreManager(
                collection_name="nl2sql_columns",
                persist_directory=self.persist_dir,
            )

            # 删除的表
            for table_name in diff.removed_tables:
                self._delete_table_columns(vector_store, db_id, table_name)
                logger.debug(f"  已删除表 {table_name} 的列向量")

            # 新增的表
            if diff.added_tables:
                added = self._upsert_table_columns(
                    vector_store, db_id, diff.added_tables, current_schemas,
                )
                logger.debug(f"  已新增 {added} 个列向量（来自 {len(diff.added_tables)} 张表）")

            # 修改的表（新增/删除/修改列）
            for table_name, table_diff in diff.modified_tables.items():
                self._update_table_columns(
                    vector_store, db_id, table_name, table_diff, current_schemas,
                )

            return ModuleReport(
                status="updated",
                details=(
                    f"新增 {len(diff.added_tables)} 表, "
                    f"删除 {len(diff.removed_tables)} 表, "
                    f"修改 {len(diff.modified_tables)} 表"
                ),
            )

        except Exception as e:
            logger.error(f"Schema Index 增量更新失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return ModuleReport(status="failed", details=str(e))

    @staticmethod
    def _delete_table_columns(
        vector_store: VectorStoreManager,
        db_id: str,
        table_name: str,
    ):
        """从 ChromaDB 删除指定表的所有列向量"""
        try:
            vector_store.collection.delete(
                where={"database": db_id, "table_name": table_name}
            )
        except Exception as e:
            logger.warning(f"删除列向量失败 ({db_id}.{table_name}): {e}")

    def _upsert_table_columns(
        self,
        vector_store: VectorStoreManager,
        db_id: str,
        table_names: List[str],
        current_schemas: dict,
        vectorizer=None,
    ) -> int:
        """将指定表的列 upsert 到 ChromaDB"""
        from src.preprocessing.schema_doc_generator import SchemaColumnDocGenerator
        from src.preprocessing.schema_vectorizer import SchemaVectorizer

        if vectorizer is None:
            vectorizer = SchemaVectorizer(model_name=self.bge_model_path, device="cpu")
            vectorizer.load_model()

        items = []
        for table_name in table_names:
            schema = current_schemas.get(table_name, {})
            foreign_keys = {fk["column"]: fk for fk in schema.get("foreign_keys", [])}

            for col in schema.get("columns", []):
                col_name = col["name"]
                col_schema = dict(col)
                if col_name in foreign_keys:
                    fk = foreign_keys[col_name]
                    col_schema["is_foreign_key"] = True
                    col_schema["references_table"] = fk["references_table"]
                    col_schema["references_column"] = fk["references_column"]
                else:
                    col_schema["is_foreign_key"] = False
                col_schema["sample_values"] = schema.get("sample_values", {}).get(col_name, [])

                doc_meta = SchemaColumnDocGenerator.build_doc_from_connector_schema(
                    database=db_id,
                    table_name=table_name,
                    col_schema=col_schema,
                )
                items.append({
                    "id": f"{db_id}.{table_name}.{col_name}",
                    "document": doc_meta["document"],
                    "metadata": doc_meta["metadata"],
                })

        if not items:
            return 0

        # 批量向量化
        documents = [i["document"] for i in items]
        embeddings = vectorizer.embed_texts(documents, return_dense=True)
        dense_vectors = embeddings.get("dense", [])

        chroma_items = []
        for i, item in enumerate(items):
            chroma_items.append({
                "id": item["id"],
                "embedding": dense_vectors[i] if i < len(dense_vectors) else None,
                "metadata": item["metadata"],
                "document": item["document"],
            })

        vector_store.add_embeddings(chroma_items)
        return len(chroma_items)

    def _update_table_columns(
        self,
        vector_store: VectorStoreManager,
        db_id: str,
        table_name: str,
        table_diff: TableDiff,
        current_schemas: dict,
    ):
        """增量更新单表的列（新增/删除/修改列）"""
        from src.preprocessing.schema_vectorizer import SchemaVectorizer
        from src.preprocessing.schema_doc_generator import SchemaColumnDocGenerator

        # 删除列
        for col_name in table_diff.removed_columns:
            try:
                vector_store.collection.delete(
                    ids=[f"{db_id}.{table_name}.{col_name}"]
                )
                logger.debug(f"  已删除列: {db_id}.{table_name}.{col_name}")
            except Exception as e:
                logger.warning(f"删除列向量失败 ({db_id}.{table_name}.{col_name}): {e}")

        # 新增列（需要向量化）
        if table_diff.added_columns:
            vectorizer = SchemaVectorizer(model_name=self.bge_model_path, device="cpu")
            vectorizer.load_model()

            schema = current_schemas.get(table_name, {})
            foreign_keys = {fk["column"]: fk for fk in schema.get("foreign_keys", [])}

            items = []
            for col_name in table_diff.added_columns:
                col = next((c for c in schema.get("columns", []) if c["name"] == col_name), None)
                if col is None:
                    continue
                col_schema = dict(col)
                if col_name in foreign_keys:
                    fk = foreign_keys[col_name]
                    col_schema["is_foreign_key"] = True
                    col_schema["references_table"] = fk["references_table"]
                    col_schema["references_column"] = fk["references_column"]
                else:
                    col_schema["is_foreign_key"] = False
                col_schema["sample_values"] = schema.get("sample_values", {}).get(col_name, [])

                doc_meta = SchemaColumnDocGenerator.build_doc_from_connector_schema(
                    database=db_id,
                    table_name=table_name,
                    col_schema=col_schema,
                )
                items.append({
                    "id": f"{db_id}.{table_name}.{col_name}",
                    "document": doc_meta["document"],
                    "metadata": doc_meta["metadata"],
                })

            if items:
                documents = [i["document"] for i in items]
                embeddings = vectorizer.embed_texts(documents, return_dense=True)
                dense_vectors = embeddings.get("dense", [])

                chroma_items = []
                for i, item in enumerate(items):
                    chroma_items.append({
                        "id": item["id"],
                        "embedding": dense_vectors[i] if i < len(dense_vectors) else None,
                        "metadata": item["metadata"],
                        "document": item["document"],
                    })

                vector_store.add_embeddings(chroma_items)
                logger.debug(f"  已新增 {len(chroma_items)} 个列向量（表 {table_name}）")

        # 修改列类型 → upsert 覆盖
        if table_diff.changed_columns:
            # 修改列可以当作"删除旧 + 新增"处理
            for col_name in table_diff.changed_columns:
                # 先删除旧向量
                try:
                    vector_store.collection.delete(
                        ids=[f"{db_id}.{table_name}.{col_name}"]
                    )
                except Exception:
                    pass

            # 重新 upsert（当作新增列处理）
            self._update_table_columns(
                vector_store, db_id, table_name,
                TableDiff(added_columns=list(table_diff.changed_columns.keys())),
                current_schemas,
            )

    # ======================================================================
    # ② Schema Graph 增量更新
    # ======================================================================

    def _update_schema_graph(
        self,
        db_id: str,
        diff: DiffResult,
        current_schemas: dict,
        needs_full: bool = False,
        cascade: bool = False,
    ) -> ModuleReport:
        """Schema Graph (JSON) 增量更新"""
        if needs_full:
            logger.info(f"{db_id}: Schema Graph 尚未构建，需要全量构建")
            return ModuleReport(status="skipped", details="未构建，请先运行 build_schema_graphs")

        if not diff.has_changes and not cascade:
            return ModuleReport(status="skipped", details="无变更")

        try:
            output_dir = str(Path(self.data_dir) / "preprocessed" / "schema_graphs")
            graph_path = os.path.join(output_dir, f"{db_id}.json")

            # 加载已有图（不存在则新建）
            if os.path.exists(graph_path):
                graph = SchemaGraphBuilder.load(graph_path)
            else:
                graph = {db_id: {"nodes": {}, "edges": []}}

            graph_data = graph.get(db_id, {"nodes": {}, "edges": []})

            # 获取 DB 连接器
            from src.preprocessing.database_connector import DatabaseConnector
            from src.preprocessing.build_schema_index import find_bird_databases
            db_map = find_bird_databases(self.data_dir)
            db_path = db_map.get(db_id)
            if not db_path:
                return ModuleReport(status="failed", details=f"未找到数据库: {db_id}")

            connector = DatabaseConnector(db_path, db_type="sqlite")

            # 初始化 builder（用于 Stage 2/3 的匹配逻辑）
            vector_store = VectorStoreManager(
                collection_name="nl2sql_columns",
                persist_directory=self.persist_dir,
            )
            builder = SchemaGraphBuilder(
                db_connector=connector,
                vector_store=vector_store,
                llm_client=self.llm_client if not self.skip_llm else None,
                hit_rate_threshold=self.hit_rate_threshold,
                top_similar_pairs=self.top_similar_pairs,
                sample_size=self.sample_size,
            )

            # --- 删除表 ---
            for table_name in diff.removed_tables:
                self._remove_table_from_graph(graph_data, table_name)
                logger.debug(f"  已从图删除表: {table_name}")

            # --- 新增表 ---
            if diff.added_tables:
                self._add_new_tables_to_graph(
                    builder, graph_data, diff.added_tables,
                    current_schemas, db_id,
                )

            # --- 修改的表 ---
            for table_name, table_diff in diff.modified_tables.items():
                self._modify_table_in_graph(
                    builder, graph_data, table_name, table_diff,
                    current_schemas, db_id,
                )

            # 更新 nodes（确保所有表都有 node 信息）
            all_table_names = set(graph_data.get("nodes", {}).keys())
            for table_name, schema in current_schemas.items():
                if table_name not in all_table_names:
                    graph_data.setdefault("nodes", {})[table_name] = {
                        "columns": [col["name"] for col in schema.get("columns", [])]
                    }

            graph[db_id] = graph_data
            SchemaGraphBuilder.save(graph, graph_path)
            connector.disconnect()

            return ModuleReport(
                status="updated",
                details=(
                    f"+{len(diff.added_tables)} 表, "
                    f"-{len(diff.removed_tables)} 表, "
                    f"~{len(diff.modified_tables)} 表"
                ),
            )

        except Exception as e:
            logger.error(f"Schema Graph 增量更新失败 ({db_id}): {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return ModuleReport(status="failed", details=str(e))

    @staticmethod
    def _remove_table_from_graph(graph_data: dict, table_name: str):
        """从图中删除指定表的 node 和所有相关 edge"""
        # 删除 node
        if "nodes" in graph_data and table_name in graph_data["nodes"]:
            del graph_data["nodes"][table_name]

        # 删除相关 edge
        if "edges" in graph_data:
            graph_data["edges"] = [
                e for e in graph_data["edges"]
                if e.get("from") != table_name and e.get("to") != table_name
            ]

    def _add_new_tables_to_graph(
        self,
        builder: SchemaGraphBuilder,
        graph_data: dict,
        added_tables: List[str],
        current_schemas: dict,
        db_id: str,
    ):
        """将新增表加入图（Stage 1/2/3 vs 所有已有表）"""
        nodes = graph_data.setdefault("nodes", {})
        edges = graph_data.setdefault("edges", [])

        # 收集已有表名
        existing_tables = [t for t in nodes.keys() if t not in added_tables]
        all_table_names = existing_tables + added_tables
        all_schemas = {**current_schemas}

        for new_table in added_tables:
            # 添加 node
            schema = current_schemas.get(new_table, {})
            nodes[new_table] = {
                "columns": [col["name"] for col in schema.get("columns", [])]
            }

            # 对每个已有表做 Stage 1/2/3
            for existing_table in existing_tables:
                if existing_table not in all_schemas:
                    continue

                # Stage 1: FK
                pair = f"{min(new_table, existing_table)}__{max(new_table, existing_table)}"
                if pair in {_pair_key(e["from"], e["to"]) for e in edges}:
                    continue

                # 检查新表是否有 FK 指向已有表
                fk_edges, connected_pairs = builder._stage1_explicit_fk(
                    {new_table: all_schemas.get(new_table, {}),
                     existing_table: all_schemas.get(existing_table, {})}
                )
                for e in fk_edges:
                    if e not in edges:
                        edges.append(e)

            # Stage 2: 向量匹配（新表 vs 所有已有表）
            # 重新检查哪些表对还没有连接
            connected = {_pair_key(e["from"], e["to"]) for e in edges}
            unconnected_existing = [
                t for t in existing_tables
                if _pair_key(new_table, t) not in connected
            ]

            if unconnected_existing:
                # 只构建新表+未连接已有表的 schema 子集
                partial_schemas = {new_table: all_schemas.get(new_table, {})}
                for t in unconnected_existing:
                    partial_schemas[t] = all_schemas.get(t, {})

                # 使用 builder._stage2_vector_similarity
                stage2_edges = builder._stage2_vector_similarity(
                    db_id,
                    [new_table] + unconnected_existing,
                    partial_schemas,
                    connected,
                )
                for e in stage2_edges:
                    if e not in edges:
                        edges.append(e)
                        connected.add(_pair_key(e["from"], e["to"]))

            # Stage 3: LLM 辅助
            remaining_unconnected = [
                t for t in unconnected_existing
                if _pair_key(new_table, t) not in connected
            ]
            if remaining_unconnected and not self.skip_llm and self.llm_client:
                partial_schemas = {new_table: all_schemas.get(new_table, {})}
                for t in remaining_unconnected:
                    partial_schemas[t] = all_schemas.get(t, {})

                stage3_edges = builder._stage3_llm_auxiliary(
                    db_id,
                    [new_table] + remaining_unconnected,
                    partial_schemas,
                    connected,
                )
                for e in stage3_edges:
                    if e not in edges:
                        edges.append(e)

    def _modify_table_in_graph(
        self,
        builder: SchemaGraphBuilder,
        graph_data: dict,
        table_name: str,
        table_diff: TableDiff,
        current_schemas: dict,
        db_id: str,
    ):
        """增量更新图中单表的变更"""
        nodes = graph_data.setdefault("nodes", {})
        edges = graph_data.setdefault("edges", [])

        # 更新 node 的列信息
        if table_name in nodes:
            schema = current_schemas.get(table_name, {})
            nodes[table_name] = {
                "columns": [col["name"] for col in schema.get("columns", [])]
            }

        # --- 删除列：清理受影响的 join_key ---
        for col_name in table_diff.removed_columns:
            full_col_ref = f"{table_name}.{col_name}"
            for edge in edges:
                if edge.get("from") != table_name and edge.get("to") != table_name:
                    continue
                original_keys = edge.get("join_keys", [])
                new_keys = []
                for jk in original_keys:
                    if len(jk) == 2:
                        # 如果 join_key 中任意一端引用了被删除的列，则移除
                        if full_col_ref in jk or (
                            jk[0] == col_name or jk[1] == col_name
                        ):
                            continue
                    new_keys.append(jk)
                edge["join_keys"] = new_keys

            # 删除空 join_key 的边
            edges[:] = [e for e in edges if e.get("join_keys")]

        # --- 新增列：只对未连接表做 Stage 2 匹配 ---
        if table_diff.added_columns:
            connected = {_pair_key(e["from"], e["to"]) for e in edges}
            existing_tables = [
                t for t in nodes.keys()
                if t != table_name and _pair_key(table_name, t) not in connected
            ]

            if existing_tables:
                # 只拿新增列的向量去 ChromaDB 匹配
                from src.preprocessing.schema_vectorizer import SchemaVectorizer
                from src.preprocessing.schema_doc_generator import SchemaColumnDocGenerator

                vectorizer = SchemaVectorizer(model_name=self.bge_model_path, device="cpu")
                vectorizer.load_model()

                vector_store = VectorStoreManager(
                    collection_name="nl2sql_columns",
                    persist_directory=self.persist_dir,
                )

                schema = current_schemas.get(table_name, {})
                foreign_keys = {fk["column"]: fk for fk in schema.get("foreign_keys", [])}

                # 为新增列生成文档和向量
                new_col_items = []
                for col_name in table_diff.added_columns:
                    col = next((c for c in schema.get("columns", []) if c["name"] == col_name), None)
                    if col is None:
                        continue
                    col_schema = dict(col)
                    if col_name in foreign_keys:
                        fk = foreign_keys[col_name]
                        col_schema["is_foreign_key"] = True
                        col_schema["references_table"] = fk["references_table"]
                        col_schema["references_column"] = fk["references_column"]
                    else:
                        col_schema["is_foreign_key"] = False
                    col_schema["sample_values"] = schema.get("sample_values", {}).get(col_name, [])

                    doc_meta = SchemaColumnDocGenerator.build_doc_from_connector_schema(
                        database=db_id,
                        table_name=table_name,
                        col_schema=col_schema,
                    )
                    new_col_items.append({
                        "id": f"{db_id}.{table_name}.{col_name}",
                        "document": doc_meta["document"],
                        "metadata": doc_meta["metadata"],
                    })

                if new_col_items:
                    # 对每个未连接表，用新增列向量去匹配
                    for existing_table in existing_tables:
                        if self._try_match_new_columns(
                            builder, vector_store, vectorizer,
                            db_id, table_name, existing_table,
                            new_col_items, current_schemas, edges,
                        ):
                            connected.add(_pair_key(table_name, existing_table))

        # --- 修改列类型：重验证受影响 join_key 的类型兼容性 ---
        if table_diff.changed_columns:
            for col_name in table_diff.changed_columns:
                full_col_ref = f"{table_name}.{col_name}"
                for edge in edges:
                    if edge.get("from") != table_name and edge.get("to") != table_name:
                        continue
                    original_keys = edge.get("join_keys", [])
                    new_keys = []
                    for jk in original_keys:
                        if len(jk) == 2:
                            # 检查该 join_key 是否涉及被修改类型的列
                            if full_col_ref in jk or jk[0] == col_name or jk[1] == col_name:
                                # 需要重验证类型兼容性
                                # 获取两边的类型
                                other_table = edge["to"] if edge["from"] == table_name else edge["from"]
                                jk_col_a = jk[0].split(".")[-1]
                                jk_col_b = jk[1].split(".")[-1]
                                type_a = self._get_col_type(current_schemas, table_name, jk_col_a)
                                type_b = self._get_col_type(current_schemas, other_table, jk_col_b)
                                if type_a and type_b and SchemaGraphBuilder._types_compatible(type_a, type_b):
                                    new_keys.append(jk)
                                # 不兼容则丢弃该 join_key
                            else:
                                new_keys.append(jk)
                    edge["join_keys"] = new_keys
                # 删除空 join_key 的边
                edges[:] = [e for e in edges if e.get("join_keys")]

    def _try_match_new_columns(
        self,
        builder: SchemaGraphBuilder,
        vector_store: VectorStoreManager,
        vectorizer,
        db_id: str,
        table_a: str,
        table_b: str,
        new_col_items: List[dict],
        current_schemas: dict,
        edges: list,
    ) -> bool:
        """用新增列的向量匹配另一张表，发现新关系"""
        # 获取表 B 的所有列向量
        try:
            results = vector_store.collection.get(
                where={"database": db_id, "table_name": table_b},
                include=["metadatas", "documents", "embeddings"],
            )
        except Exception:
            return False

        if not results or not results["ids"]:
            return False

        # 构建表 B 的列信息
        b_columns = []
        for i in range(len(results["ids"])):
            meta = results["metadatas"][i] if results["metadatas"] else {}
            embeddings_data = results.get("embeddings")
            embedding = embeddings_data[i] if embeddings_data is not None else None
            b_columns.append({
                "id": results["ids"][i],
                "metadata": meta,
                "embedding": embedding,
            })

        # 对每个新增列，与表 B 所有列计算余弦相似度
        for col_item in new_col_items:
            col_embedding = None
            # 需要先向量化新增列的文档
            doc_embedding = vectorizer.embed_texts([col_item["document"]], return_dense=True)
            dense = doc_embedding.get("dense", [])
            if dense:
                col_embedding = dense[0]
            if col_embedding is None:
                continue

            # 与表 B 列计算相似度
            candidates = []
            for b_col in b_columns:
                b_embedding = b_col.get("embedding")
                if b_embedding is None:
                    continue
                sim = SchemaGraphBuilder._cosine_similarity(col_embedding, b_embedding)
                if sim > 0:
                    col_a_name = col_item["id"].split(".")[-1]
                    b_meta = b_col.get("metadata", {})
                    b_col_name = b_meta.get("original_column_name", "")
                    b_dtype = b_meta.get("data_type", "TEXT")
                    a_dtype = self._get_col_type(current_schemas, table_a, col_a_name) or "TEXT"
                    if SchemaGraphBuilder._types_compatible(a_dtype, b_dtype):
                        candidates.append({
                            "col_a": col_a_name,
                            "col_b": b_col_name,
                            "similarity": sim,
                        })

            if not candidates:
                continue

            # 取 top N
            candidates.sort(key=lambda x: x["similarity"], reverse=True)
            top_candidates = candidates[:builder.top_similar_pairs]

            # 命中率验证
            similar_pairs = [(c["col_a"], c["col_b"], c["similarity"]) for c in top_candidates]
            verified = builder._verify_value_overlap(
                similar_pairs,
                {table_a: current_schemas.get(table_a, {}), table_b: current_schemas.get(table_b, {})}
            )

            if verified:
                edges.append({
                    "from": table_a,
                    "to": table_b,
                    "join_keys": [[f"{table_a}.{jk[0]}", f"{table_b}.{jk[1]}"] for jk in verified],
                    "type": "vector_similarity",
                })
                return True

        return False

    @staticmethod
    def _get_col_type(schemas: dict, table_name: str, col_name: str) -> Optional[str]:
        """从 current_schemas 获取列类型"""
        schema = schemas.get(table_name, {})
        for col in schema.get("columns", []):
            if col["name"] == col_name:
                return col.get("type", "TEXT")
        return None

    # ======================================================================
    # ③ LSH Index 增量更新
    # ======================================================================

    def _update_lsh_index(
        self,
        db_id: str,
        diff: DiffResult,
        needs_full: bool = False,
    ) -> ModuleReport:
        """LSH Index (Pickle) 增量更新"""
        if needs_full:
            logger.info(f"{db_id}: LSH Index 尚未构建，需要全量构建")
            return ModuleReport(status="skipped", details="未构建，请先运行 build_lsh_index")

        if not (diff.added_tables or diff.removed_tables
                or any(t.has_changes for t in diff.modified_tables.values())):
            return ModuleReport(status="skipped", details="无变更")

        try:
            from src.preprocessing.build_schema_index import find_bird_databases
            db_map = find_bird_databases(self.data_dir)
            db_directory = db_map.get(db_id)
            if not db_directory:
                return ModuleReport(status="failed", details=f"未找到数据库: {db_id}")

            preprocessed_dir = Path(db_directory) / "preprocessed"
            lsh_path = preprocessed_dir / f"{db_id}_lsh.pkl"
            minhash_path = preprocessed_dir / f"{db_id}_minhashes.pkl"
            unique_values_path = preprocessed_dir / f"{db_id}_unique_values.pkl"

            # 检查 LSH 索引是否存在
            if not lsh_path.exists() or not minhash_path.exists():
                return ModuleReport(status="skipped", details="LSH 索引尚未构建，跳过")

            # 加载已有索引
            with open(lsh_path, "rb") as f:
                lsh = pickle.load(f)
            with open(minhash_path, "rb") as f:
                minhashes = pickle.load(f)
            with open(unique_values_path, "rb") as f:
                old_unique_values = pickle.load(f)

            indexer = LSHIndexer()

            # 获取当前唯一值（全量，用于对比和新增）
            db_file = Path(db_directory) / f"{db_id}.sqlite"
            if not db_file.exists():
                db_file = Path(db_directory) / f"{db_id}.db"
            current_unique_values = LSHIndexer.get_unique_values(str(db_file))

            # --- 删除表 ---
            for table_name in diff.removed_tables:
                self._remove_table_from_lsh(lsh, minhashes, table_name)
                old_unique_values.pop(table_name, None)

            # --- 新增表 ---
            for table_name in diff.added_tables:
                if table_name in current_unique_values:
                    self._add_table_to_lsh(
                        indexer, lsh, minhashes, table_name,
                        current_unique_values[table_name],
                    )
                    old_unique_values[table_name] = current_unique_values[table_name]

            # --- 修改表（新增/删除列） ---
            for table_name, table_diff in diff.modified_tables.items():
                if table_name not in current_unique_values:
                    continue

                old_col_values = old_unique_values.get(table_name, {})
                new_col_values = current_unique_values[table_name]

                # 删除列
                for col_name in table_diff.removed_columns:
                    self._remove_column_from_lsh(lsh, minhashes, table_name, col_name)
                    old_col_values.pop(col_name, None)

                # 新增列
                for col_name in table_diff.added_columns:
                    if col_name in new_col_values:
                        self._add_column_to_lsh(
                            indexer, lsh, minhashes, table_name, col_name,
                            new_col_values[col_name],
                        )
                        old_col_values[col_name] = new_col_values[col_name]

                # 修改列（先删后增）
                for col_name in table_diff.changed_columns:
                    self._remove_column_from_lsh(lsh, minhashes, table_name, col_name)
                    if col_name in new_col_values:
                        self._add_column_to_lsh(
                            indexer, lsh, minhashes, table_name, col_name,
                            new_col_values[col_name],
                        )
                        old_col_values[col_name] = new_col_values[col_name]

            # 保存更新后的索引
            preprocessed_dir.mkdir(exist_ok=True)

            with open(lsh_path, "wb") as f:
                pickle.dump(lsh, f)
            with open(minhash_path, "wb") as f:
                pickle.dump(minhashes, f)
            with open(unique_values_path, "wb") as f:
                pickle.dump(old_unique_values, f)

            return ModuleReport(
                status="updated",
                details=(
                    f"+{len(diff.added_tables)} 表, "
                    f"-{len(diff.removed_tables)} 表, "
                    f"~{len(diff.modified_tables)} 表"
                ),
            )

        except Exception as e:
            logger.error(f"LSH Index 增量更新失败 ({db_id}): {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return ModuleReport(status="failed", details=str(e))

    @staticmethod
    def _remove_table_from_lsh(lsh, minhashes: dict, table_name: str):
        """从 LSH 索引中删除指定表的所有 key"""
        keys_to_remove = [
            key for key in minhashes
            if key.startswith(f"{table_name}_")
        ]
        for key in keys_to_remove:
            try:
                lsh.remove(key)
            except Exception:
                pass
            del minhashes[key]

    @staticmethod
    def _add_table_to_lsh(
        indexer: LSHIndexer,
        lsh,
        minhashes: dict,
        table_name: str,
        column_values: dict,
    ):
        """将新表的所有 TEXT 列加入 LSH 索引"""
        for column_name, values in column_values.items():
            IncrementalUpdater._add_column_to_lsh(
                indexer, lsh, minhashes, table_name, column_name, values,
            )

    @staticmethod
    def _add_column_to_lsh(
        indexer: LSHIndexer,
        lsh,
        minhashes: dict,
        table_name: str,
        column_name: str,
        values: list,
    ):
        """将单列的所有值加入 LSH 索引"""
        for idx, value in enumerate(values):
            try:
                mh = indexer.create_minhash(
                    value, indexer.signature_size, indexer.n_gram,
                )
                key = f"{table_name}_{column_name}_{idx}"
                minhashes[key] = (mh, table_name, column_name, value)
                lsh.insert(key, mh)
            except Exception:
                continue

    @staticmethod
    def _remove_column_from_lsh(
        lsh,
        minhashes: dict,
        table_name: str,
        column_name: str,
    ):
        """从 LSH 索引中删除指定列的所有 key"""
        prefix = f"{table_name}_{column_name}_"
        keys_to_remove = [key for key in minhashes if key.startswith(prefix)]
        for key in keys_to_remove:
            try:
                lsh.remove(key)
            except Exception:
                pass
            del minhashes[key]

    # ======================================================================
    # 辅助方法
    # ======================================================================

    def _get_current_schemas(self, db_id: str) -> dict:
        """读取当前 DB schema"""
        try:
            from src.preprocessing.database_connector import DatabaseConnector
            from src.preprocessing.build_schema_index import find_bird_databases

            db_map = find_bird_databases(self.data_dir)
            db_path = db_map.get(db_id)
            if not db_path:
                logger.warning(f"未找到数据库: {db_id}")
                return {}

            connector = DatabaseConnector(db_path, db_type="sqlite")
            schemas = connector.get_all_schemas()
            connector.disconnect()
            return schemas

        except Exception as e:
            logger.error(f"读取数据库 schema 失败 ({db_id}): {e}")
            return {}


# ============================================================================
# 辅助函数
# ============================================================================


def _pair_key(a: str, b: str) -> str:
    """生成无序表对 key"""
    return f"{min(a, b)}__{max(a, b)}"


# ============================================================================
# 便捷函数：检测 diff（供外部代码调用）
# ============================================================================


def check_updates(db_id: str = None, data_dir: str = None) -> List[dict]:
    """
    检测指定（或所有）数据库的变更情况，不执行更新

    Args:
        db_id: 指定数据库 ID，None 表示检测所有
        data_dir: 数据目录

    Returns:
        List[dict]: 每个库的变更信息
            [{"db_id": str, "has_changes": bool, "added_tables": [...],
              "removed_tables": [...], "modified_tables": {...}}]
    """
    if data_dir is None:
        data_dir = str(Path(__file__).parent.parent.parent / "data")

    from src.preprocessing.build_schema_index import find_bird_databases
    db_map = find_bird_databases(data_dir)
    manifest = Manifest(manifest_path=str(Path(data_dir) / "manifest.json"))

    results = []
    target_dbs = [db_id] if db_id else sorted(db_map.keys())

    for db_id in target_dbs:
        try:
            connector = DatabaseConnector(db_map[db_id], db_type="sqlite")
            current_schemas = connector.get_all_schemas()
            connector.disconnect()
        except Exception:
            results.append({"db_id": db_id, "has_changes": False, "error": "连接失败"})
            continue

        old_entry = manifest.load().databases.get(db_id)
        diff = Manifest.compute_diff(old_entry, current_schemas)

        results.append({
            "db_id": db_id,
            "has_changes": diff.has_changes,
            "added_tables": diff.added_tables,
            "removed_tables": diff.removed_tables,
            "modified_tables": {
                tbl: {
                    "added_columns": td.added_columns,
                    "removed_columns": td.removed_columns,
                    "changed_columns": td.changed_columns,
                }
                for tbl, td in diff.modified_tables.items()
            },
        })

    return results


# ============================================================================
# 直接运行入口
# ============================================================================

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    logger.info("=" * 60)
    logger.info("预处理增量更新")
    logger.info("=" * 60)

    updater = IncrementalUpdater()
    reports = updater.update_all()
    updated = sum(1 for r in reports if r.diff.has_changes)
    logger.info(f"扫描完成: {updated}/{len(reports)} 个库有变更并已更新")

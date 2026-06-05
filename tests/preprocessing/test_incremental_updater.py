# ============================================================================
# 预处理增量更新器 测试用例
# ============================================================================
# 运行方法:
#   python -m pytest tests/preprocessing/test_incremental_updater.py -v
# ============================================================================


import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.preprocessing.manifest import (
    ColumnInfo,
    DatabaseManifest,
    DiffResult,
    Manifest,
    ManifestData,
    TableDiff,
    TableManifest,
    write_manifest_for_db,
)
from src.preprocessing.incremental_updater import (
    IncrementalUpdater,
    ModuleReport,
    UpdateReport,
    _pair_key,
)


# ============================================================================
# Mock 辅助
# ============================================================================


def _make_mock_schema(table_name, columns, foreign_keys=None):
    """生成模拟 schema dict"""
    schema = {
        "table_name": table_name,
        "columns": columns,
        "foreign_keys": foreign_keys or [],
        "sample_values": {},
        "row_count": 100,
    }
    for col in columns:
        schema["sample_values"][col["name"]] = [f"val_{col['name']}_1"]
    return schema


def _make_column(name, dtype="TEXT", pk=False):
    return {"name": name, "type": dtype, "primary_key": pk, "nullable": True, "default": None}


# ============================================================================
# Manifest 测试
# ============================================================================


class TestManifest(unittest.TestCase):
    """Manifest 类：load / save / compute_diff"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.manifest_path = os.path.join(self.tmpdir, "manifest.json")
        self.manifest = Manifest(manifest_path=self.manifest_path)

    def test_save_and_load(self):
        """保存后再加载，数据一致"""
        data = ManifestData(
            version=1,
            last_updated="2026-06-05T12:00:00",
            databases={
                "db1": DatabaseManifest(
                    schema_index_build_time="2026-06-05T10:00:00",
                    schema_graph_build_time="2026-06-05T10:05:00",
                    lsh_index_build_time="2026-06-05T10:10:00",
                    tables={
                        "users": TableManifest(columns={
                            "id": ColumnInfo(type="INTEGER"),
                            "name": ColumnInfo(type="TEXT"),
                        }),
                    },
                ),
            },
        )
        ok = self.manifest.save(data)
        self.assertTrue(ok)

        loaded = self.manifest.load()
        self.assertEqual(loaded.version, 1)
        self.assertIn("db1", loaded.databases)
        self.assertIn("users", loaded.databases["db1"].tables)
        self.assertEqual(loaded.databases["db1"].schema_index_build_time, "2026-06-05T10:00:00")
        self.assertEqual(loaded.databases["db1"].schema_graph_build_time, "2026-06-05T10:05:00")
        self.assertEqual(loaded.databases["db1"].lsh_index_build_time, "2026-06-05T10:10:00")

    def test_load_nonexistent_returns_empty(self):
        """Manifest 文件不存在时返回空 ManifestData"""
        data = self.manifest.load()
        self.assertEqual(data.version, 1)
        self.assertEqual(data.databases, {})

    def test_load_old_format_compatible(self):
        """兼容旧格式：build_time 自动填充三个字段"""
        raw = {
            "version": 1,
            "last_updated": "2026-06-05T12:00:00",
            "databases": {
                "db1": {
                    "build_time": "2026-06-05T12:00:00",
                    "tables": {
                        "users": {"columns": {"id": {"type": "INTEGER"}}}
                    },
                },
            },
        }
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(raw, f)

        loaded = self.manifest.load()
        entry = loaded.databases["db1"]
        self.assertEqual(entry.schema_index_build_time, "2026-06-05T12:00:00")
        self.assertEqual(entry.schema_graph_build_time, "2026-06-05T12:00:00")
        self.assertEqual(entry.lsh_index_build_time, "2026-06-05T12:00:00")

    def test_build_from_schema(self):
        """从 schema 构建 Manifest 条目"""
        schemas = {
            "users": _make_mock_schema(
                "users",
                columns=[_make_column("id", "INTEGER", pk=True), _make_column("name", "TEXT")],
                foreign_keys=[],
            ),
            "orders": _make_mock_schema(
                "orders",
                columns=[_make_column("id", "INTEGER", pk=True), _make_column("user_id", "INTEGER")],
                foreign_keys=[{"column": "user_id", "references_table": "users", "references_column": "id"}],
            ),
        }
        entry = Manifest.build_manifest_from_schema("test_db", schemas)

        # 新格式不设置 build_time（由各构建脚本各自写入）
        self.assertIsNone(entry.schema_index_build_time)
        self.assertIsNone(entry.schema_graph_build_time)
        self.assertIsNone(entry.lsh_index_build_time)
        self.assertIn("users", entry.tables)
        self.assertIn("orders", entry.tables)
        self.assertTrue(entry.tables["orders"].columns["user_id"].is_fk)
        self.assertEqual(entry.tables["orders"].columns["user_id"].references, "users.id")

    def test_compute_diff_added_table(self):
        """新增表检测"""
        diff = Manifest.compute_diff(
            DatabaseManifest(tables={
                "users": TableManifest(columns={"id": ColumnInfo(type="INTEGER")}),
            }),
            {
                "users": _make_mock_schema("users", columns=[_make_column("id", "INTEGER")]),
                "orders": _make_mock_schema("orders", columns=[_make_column("id", "INTEGER")]),
            },
        )
        self.assertEqual(diff.added_tables, ["orders"])
        self.assertTrue(diff.has_changes)

    def test_compute_diff_removed_table(self):
        """删除表检测"""
        diff = Manifest.compute_diff(
            DatabaseManifest(tables={
                "users": TableManifest(columns={"id": ColumnInfo(type="INTEGER")}),
                "orders": TableManifest(columns={"id": ColumnInfo(type="INTEGER")}),
            }),
            {
                "users": _make_mock_schema("users", columns=[_make_column("id", "INTEGER")]),
            },
        )
        self.assertEqual(diff.removed_tables, ["orders"])

    def test_compute_diff_added_column(self):
        """新增列检测"""
        diff = Manifest.compute_diff(
            DatabaseManifest(tables={
                "users": TableManifest(columns={"id": ColumnInfo(type="INTEGER")}),
            }),
            {
                "users": _make_mock_schema("users", columns=[
                    _make_column("id", "INTEGER"), _make_column("name", "TEXT"),
                ]),
            },
        )
        self.assertEqual(diff.modified_tables["users"].added_columns, ["name"])

    def test_compute_diff_removed_column(self):
        """删除列检测"""
        diff = Manifest.compute_diff(
            DatabaseManifest(tables={
                "users": TableManifest(columns={
                    "id": ColumnInfo(type="INTEGER"), "name": ColumnInfo(type="TEXT"),
                }),
            }),
            {
                "users": _make_mock_schema("users", columns=[_make_column("id", "INTEGER")]),
            },
        )
        self.assertEqual(diff.modified_tables["users"].removed_columns, ["name"])

    def test_compute_diff_changed_column_type(self):
        """列类型变化检测"""
        diff = Manifest.compute_diff(
            DatabaseManifest(tables={
                "users": TableManifest(columns={
                    "id": ColumnInfo(type="INTEGER"),
                    "name": ColumnInfo(type="TEXT"),
                }),
            }),
            {
                "users": _make_mock_schema("users", columns=[
                    _make_column("id", "INTEGER"), _make_column("name", "VARCHAR(100)"),
                ]),
            },
        )
        self.assertIn("name", diff.modified_tables["users"].changed_columns)
        self.assertEqual(diff.modified_tables["users"].changed_columns["name"]["old_type"], "TEXT")
        self.assertEqual(diff.modified_tables["users"].changed_columns["name"]["new_type"], "VARCHAR(100)")

    def test_compute_diff_no_changes(self):
        """无变更时返回空 diff"""
        diff = Manifest.compute_diff(
            DatabaseManifest(tables={
                "users": TableManifest(columns={"id": ColumnInfo(type="INTEGER")}),
            }),
            {
                "users": _make_mock_schema("users", columns=[_make_column("id", "INTEGER")]),
            },
        )
        self.assertFalse(diff.has_changes)

    def test_compute_diff_new_db(self):
        """全新库：所有表都是新增"""
        diff = Manifest.compute_diff(None, {
            "users": _make_mock_schema("users", columns=[_make_column("id", "INTEGER")]),
            "orders": _make_mock_schema("orders", columns=[_make_column("id", "INTEGER")]),
        })
        self.assertEqual(sorted(diff.added_tables), ["orders", "users"])

    def test_update_build_time(self):
        """update_build_time 独立更新各模块"""
        data = ManifestData(databases={
            "db1": DatabaseManifest(
                schema_index_build_time="2026-06-05T10:00:00",
                tables={"users": TableManifest(columns={"id": ColumnInfo(type="INTEGER")})},
            ),
        })
        self.manifest.save(data)

        # 更新 Schema Graph 的 build_time
        self.manifest.update_build_time("db1", "schema_graph")

        loaded = self.manifest.load()
        self.assertEqual(loaded.databases["db1"].schema_index_build_time, "2026-06-05T10:00:00")
        self.assertIsNotNone(loaded.databases["db1"].schema_graph_build_time)
        self.assertIsNone(loaded.databases["db1"].lsh_index_build_time)

    def test_update_build_time_unknown_module(self):
        """未知模块名"""
        data = ManifestData(databases={"db1": DatabaseManifest()})
        self.manifest.save(data)
        ok = self.manifest.update_build_time("db1", "unknown_module")
        self.assertFalse(ok)

    def test_independent_build_times(self):
        """三模块独立 build_time：各只写自己的"""
        data = ManifestData(databases={
            "db1": DatabaseManifest(tables={
                "users": TableManifest(columns={"id": ColumnInfo(type="INTEGER")})}),
        })
        self.manifest.save(data)

        self.manifest.update_build_time("db1", "schema_index")
        self.manifest.update_build_time("db1", "lsh_index")

        loaded = self.manifest.load()
        self.assertIsNotNone(loaded.databases["db1"].schema_index_build_time)
        self.assertIsNone(loaded.databases["db1"].schema_graph_build_time)  # 没写
        self.assertIsNotNone(loaded.databases["db1"].lsh_index_build_time)


# ============================================================================
# IncrementalUpdater 测试
# ============================================================================


class TestIncrementalUpdater(unittest.TestCase):
    """IncrementalUpdater 类"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.data_dir = os.path.join(self.tmpdir, "data")
        os.makedirs(os.path.join(self.data_dir, "preprocessed", "chroma"), exist_ok=True)
        os.makedirs(os.path.join(self.data_dir, "preprocessed", "schema_graphs"), exist_ok=True)

        self.manifest_path = os.path.join(self.data_dir, "preprocessed", "manifest.json")
        self.updater = IncrementalUpdater(
            data_dir=self.data_dir,
            manifest_path=self.manifest_path,
            skip_llm=True,
        )

    def test_pair_key_symmetry(self):
        """_pair_key 对称性"""
        self.assertEqual(_pair_key("a", "b"), _pair_key("b", "a"))

    def test_module_report_defaults(self):
        """ModuleReport 默认值"""
        r = ModuleReport()
        self.assertEqual(r.status, "skipped")
        self.assertEqual(r.details, "")

    def test_update_report_success_property(self):
        """UpdateReport.success 属性"""
        report = UpdateReport()
        self.assertTrue(report.success)
        report.schema_index = ModuleReport(status="failed")
        self.assertFalse(report.success)


class TestIncrementalUpdaterSchemaIndex(TestIncrementalUpdater):
    """Schema Index 增量更新测试"""

    @patch("src.preprocessing.incremental_updater.VectorStoreManager")
    def test_delete_table_columns(self, mock_vs_class):
        """删除表的列向量"""
        mock_vs = MagicMock()
        mock_vs_class.return_value = mock_vs
        IncrementalUpdater._delete_table_columns(mock_vs, "test_db", "users")
        mock_vs.collection.delete.assert_called_once_with(
            where={"database": "test_db", "table_name": "users"}
        )

    @patch("src.preprocessing.incremental_updater.VectorStoreManager")
    def test_update_schema_index_no_changes(self, mock_vs_class):
        """无变更时跳过"""
        diff = DiffResult()
        report = self.updater._update_schema_index("test_db", diff, {})
        self.assertEqual(report.status, "skipped")

    @patch("src.preprocessing.incremental_updater.VectorStoreManager")
    def test_update_schema_index_delete_table(self, mock_vs_class):
        """删除表的 Schema Index 增量"""
        mock_vs = MagicMock()
        mock_vs_class.return_value = mock_vs
        diff = DiffResult(removed_tables=["users"])
        report = self.updater._update_schema_index("test_db", diff, {})
        mock_vs.collection.delete.assert_called_once_with(
            where={"database": "test_db", "table_name": "users"}
        )
        self.assertEqual(report.status, "updated")

    @patch("src.preprocessing.incremental_updater.VectorStoreManager")
    def test_update_schema_index_needs_full(self, mock_vs_class):
        """Schema Index 未构建时返回 skipped"""
        diff = DiffResult(added_tables=["users"])
        report = self.updater._update_schema_index(
            "test_db", diff, {}, needs_full=True
        )
        self.assertEqual(report.status, "skipped")
        self.assertIn("未构建", report.details)


class TestIncrementalUpdaterSchemaGraph(TestIncrementalUpdater):
    """Schema Graph 增量更新测试"""

    def setUp(self):
        super().setUp()
        self.graph_dir = os.path.join(self.data_dir, "preprocessed", "schema_graphs")
        os.makedirs(self.graph_dir, exist_ok=True)

    def test_remove_table_from_graph(self):
        """从图中删除表"""
        graph_data = {
            "nodes": {"users": {"columns": ["id", "name"]}, "orders": {"columns": ["id", "user_id"]}},
            "edges": [
                {"from": "orders", "to": "users", "join_keys": [["orders.user_id", "users.id"]], "type": "explicit_fk"},
                {"from": "users", "to": "profiles", "join_keys": [["users.id", "profiles.user_id"]], "type": "vector_similarity"},
            ],
        }
        IncrementalUpdater._remove_table_from_graph(graph_data, "users")
        self.assertNotIn("users", graph_data["nodes"])
        self.assertEqual(len(graph_data["edges"]), 0)

    def test_remove_table_from_graph_no_edges_affected(self):
        """删除的表不影响其他表的边"""
        graph_data = {
            "nodes": {"users": {"columns": ["id"]}, "orders": {"columns": ["id"]}},
            "edges": [{"from": "orders", "to": "users", "join_keys": [["orders.id", "users.id"]], "type": "fk"}],
        }
        IncrementalUpdater._remove_table_from_graph(graph_data, "orders")
        self.assertIn("users", graph_data["nodes"])
        self.assertEqual(len(graph_data["edges"]), 0)

    @patch("src.preprocessing.incremental_updater.VectorStoreManager")
    def test_schema_graph_needs_full(self, mock_vs_class):
        """Schema Graph 未构建时返回 skipped"""
        diff = DiffResult(added_tables=["users"])
        report = self.updater._update_schema_graph(
            "test_db", diff, {}, needs_full=True
        )
        self.assertEqual(report.status, "skipped")
        self.assertIn("未构建", report.details)

    @patch("src.preprocessing.incremental_updater.VectorStoreManager")
    def test_schema_graph_no_changes_no_cascade(self, mock_vs_class):
        """无变更且无级联时跳过"""
        diff = DiffResult()
        report = self.updater._update_schema_graph(
            "test_db", diff, {}, needs_full=False, cascade=False
        )
        self.assertEqual(report.status, "skipped")


class TestIncrementalUpdaterLSH(TestIncrementalUpdater):
    """LSH Index 增量更新测试"""

    def setUp(self):
        super().setUp()
        self.mock_lsh = MagicMock()
        self.mock_minhashes = {}

    def test_remove_table_from_lsh(self):
        """从 LSH 中删除表的所有 key"""
        self.mock_minhashes.update({
            "users_id_0": (MagicMock(), "users", "id", "1"),
            "users_name_0": (MagicMock(), "users", "name", "alice"),
            "orders_id_0": (MagicMock(), "orders", "id", "100"),
        })
        IncrementalUpdater._remove_table_from_lsh(self.mock_lsh, self.mock_minhashes, "users")
        self.assertNotIn("users_id_0", self.mock_minhashes)
        self.assertIn("orders_id_0", self.mock_minhashes)
        self.assertEqual(self.mock_lsh.remove.call_count, 2)

    def test_remove_column_from_lsh(self):
        """从 LSH 中删除单列的所有 key"""
        self.mock_minhashes.update({
            "users_id_0": (MagicMock(), "users", "id", "1"),
            "users_name_0": (MagicMock(), "users", "name", "alice"),
            "users_name_1": (MagicMock(), "users", "name", "bob"),
        })
        IncrementalUpdater._remove_column_from_lsh(self.mock_lsh, self.mock_minhashes, "users", "name")
        self.assertIn("users_id_0", self.mock_minhashes)
        self.assertNotIn("users_name_0", self.mock_minhashes)
        self.assertEqual(self.mock_lsh.remove.call_count, 2)

    def test_lsh_needs_full(self):
        """LSH Index 未构建时返回 skipped"""
        diff = DiffResult(added_tables=["users"])
        report = self.updater._update_lsh_index("test_db", diff, needs_full=True)
        self.assertEqual(report.status, "skipped")
        self.assertIn("未构建", report.details)


# ============================================================================
# 集成测试：全量构建后自动写入 Manifest（带 module 参数）
# ============================================================================


class TestBuildAutoManifest(unittest.TestCase):
    """全量构建脚本自动写入 Manifest"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.data_dir = os.path.join(self.tmpdir, "data")
        os.makedirs(os.path.join(self.data_dir, "preprocessed", "chroma"), exist_ok=True)
        self.manifest_path = os.path.join(self.data_dir, "preprocessed", "manifest.json")

    @patch("src.preprocessing.manifest.write_manifest_for_db")
    @patch("src.preprocessing.build_schema_index.find_bird_databases")
    @patch("src.preprocessing.build_schema_index.DatabaseConnector")
    @patch("src.preprocessing.build_schema_index.SchemaVectorizer")
    @patch("src.preprocessing.build_schema_index.VectorStoreManager")
    def test_build_schema_index_writes_manifest(
        self, mock_vs_class, mock_vec_class, mock_conn_class, mock_find_db, mock_write_mf,
    ):
        """build_schema_index_for_db 完成后写入 module='schema_index'"""
        from src.preprocessing.build_schema_index import build_schema_index_for_db

        mock_find_db.return_value = {"test_db": "/fake/path/test_db.sqlite"}
        mock_conn = MagicMock()
        mock_conn.get_tables.return_value = ["users"]
        mock_conn.get_table_schema.return_value = _make_mock_schema(
            "users", columns=[_make_column("id", "INTEGER"), _make_column("name", "TEXT")]
        )
        mock_conn_class.return_value = mock_conn

        mock_vec = MagicMock()
        mock_vec.embed_texts.return_value = {"dense": [[0.1, 0.2], [0.3, 0.4]]}
        mock_vec_class.return_value = mock_vec

        mock_vs = MagicMock()
        mock_vs_class.return_value = mock_vs

        with patch("src.preprocessing.build_schema_index.get_persist_dir", return_value=self.data_dir):
            result = build_schema_index_for_db(
                "test_db", data_dir=self.data_dir, bge_model_path="BAAI/bge-m3"
            )

        self.assertTrue(result)
        mock_write_mf.assert_called_once()
        # 验证传入 module="schema_index"
        call_args = mock_write_mf.call_args
        self.assertEqual(call_args.kwargs.get("module") or call_args[1].get("module"), "schema_index")

    @patch("src.preprocessing.manifest.write_manifest_for_db")
    @patch("src.preprocessing.build_schema_graphs.find_bird_databases")
    @patch("src.preprocessing.build_schema_graphs.DatabaseConnector")
    @patch("src.preprocessing.build_schema_graphs.VectorStoreManager")
    def test_build_schema_graphs_writes_manifest(
        self, mock_vs_class, mock_conn_class, mock_find_db, mock_write_mf,
    ):
        """build_schema_graphs 完成后写入 module='schema_graph'"""
        from src.preprocessing.build_schema_graphs import build_schema_graphs

        mock_find_db.return_value = {"test_db": "/fake/path/test_db.sqlite"}
        mock_conn = MagicMock()
        mock_conn.get_tables.return_value = ["users"]
        mock_conn.get_table_schema.return_value = _make_mock_schema(
            "users", columns=[_make_column("id", "INTEGER")]
        )
        mock_conn.get_all_schemas.return_value = {
            "users": _make_mock_schema("users", columns=[_make_column("id", "INTEGER")]),
        }
        mock_conn_class.return_value = mock_conn

        mock_vs = MagicMock()
        mock_vs.collection.get.return_value = {"ids": [], "metadatas": [], "documents": [], "embeddings": None}
        mock_vs_class.return_value = mock_vs

        result = build_schema_graphs(db_id="test_db", data_dir=self.data_dir, skip_llm=True)

        self.assertEqual(result, 1)
        mock_write_mf.assert_called_once()
        call_args = mock_write_mf.call_args
        self.assertEqual(call_args.kwargs.get("module") or call_args[1].get("module"), "schema_graph")


# ============================================================================
# 依赖检查与级联触发测试
# ============================================================================


class TestDependencyCheck(unittest.TestCase):
    """IncrementalUpdater 依赖检查与级联触发"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.data_dir = os.path.join(self.tmpdir, "data")
        os.makedirs(os.path.join(self.data_dir, "preprocessed", "chroma"), exist_ok=True)
        os.makedirs(os.path.join(self.data_dir, "preprocessed", "schema_graphs"), exist_ok=True)
        self.manifest_path = os.path.join(self.data_dir, "preprocessed", "manifest.json")

    def test_schema_graph_skipped_when_index_not_built(self):
        """Schema Index 未构建时，Schema Graph 被跳过"""
        # 写一个只有 schema_graph_build_time 的 manifest（缺少 schema_index_build_time）
        manifest = Manifest(manifest_path=self.manifest_path)
        data = ManifestData(databases={
            "db1": DatabaseManifest(
                schema_graph_build_time="2026-06-05T10:00:00",
                # schema_index_build_time 缺失
                tables={"users": TableManifest(columns={"id": ColumnInfo(type="INTEGER")})},
            ),
        })
        manifest.save(data)

        updater = IncrementalUpdater(
            data_dir=self.data_dir,
            manifest_path=self.manifest_path,
            skip_llm=True,
        )

        # 模拟 _get_current_schemas 和 _update_schema_index
        with patch.object(updater, '_get_current_schemas', return_value={
            "users": _make_mock_schema("users", columns=[_make_column("id", "INTEGER")]),
        }):
            with patch.object(updater, '_update_schema_index', return_value=ModuleReport(status="skipped")):
                with patch.object(updater, '_update_lsh_index', return_value=ModuleReport(status="skipped")):
                    report = updater.update("db1")

        # Schema Graph 应被跳过
        self.assertEqual(report.schema_graph.status, "skipped")
        self.assertIn("Schema Index 未构建", report.schema_graph.details)

    def test_cascade_triggers_graph_update(self):
        """Schema Index 有变更时，Schema Graph 即使 diff 为空也会重新处理"""
        manifest = Manifest(manifest_path=self.manifest_path)
        data = ManifestData(databases={
            "db1": DatabaseManifest(
                schema_index_build_time="2026-06-05T10:00:00",
                schema_graph_build_time="2026-06-05T10:05:00",
                lsh_index_build_time="2026-06-05T10:10:00",
                tables={"users": TableManifest(columns={"id": ColumnInfo(type="INTEGER")})},
            ),
        })
        manifest.save(data)

        updater = IncrementalUpdater(
            data_dir=self.data_dir,
            manifest_path=self.manifest_path,
            skip_llm=True,
        )

        # Schema Index 返回 "updated"，diff 无变更
        with patch.object(updater, '_get_current_schemas', return_value={
            "users": _make_mock_schema("users", columns=[_make_column("id", "INTEGER")]),
        }):
            with patch.object(updater, '_update_schema_index', return_value=ModuleReport(status="updated")):
                with patch.object(updater, '_update_schema_graph', return_value=ModuleReport(status="updated")) as mock_graph:
                    with patch.object(updater, '_update_lsh_index', return_value=ModuleReport(status="skipped")):
                        report = updater.update("db1")

        # _update_schema_graph 应被调用且 cascade=True
        mock_graph.assert_called_once()
        call_kwargs = mock_graph.call_args
        self.assertTrue(call_kwargs.kwargs.get("cascade") or call_kwargs[1].get("cascade", False))


if __name__ == "__main__":
    unittest.main()

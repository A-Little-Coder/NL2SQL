# ============================================================================
# SchemaGraphBuilder 测试用例
# ============================================================================
# 运行方法:
#   python -m unittest tests.preprocessing.test_schema_graph_builder -v
# ============================================================================


import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.preprocessing.schema_graph_builder import (
    SchemaGraphBuilder,
    _cosine_similarity,
    _find_edge,
    _pair_key,
    _types_compatible,
    extract_join_paths,
    format_join_paths_for_prompt,
)


# ============================================================================
# 工具函数测试
# ============================================================================

class TestPairKey(unittest.TestCase):
    def test_ordering(self):
        self.assertEqual(_pair_key("a", "b"), _pair_key("b", "a"))

    def test_same(self):
        self.assertEqual(_pair_key("x", "x"), "x|x")


class TestCosineSimilarity(unittest.TestCase):
    def test_identical(self):
        vec = [1.0, 0.0, 0.0]
        self.assertAlmostEqual(_cosine_similarity(vec, vec), 1.0)

    def test_orthogonal(self):
        self.assertAlmostEqual(_cosine_similarity([1, 0], [0, 1]), 0.0)

    def test_zero_vector(self):
        self.assertEqual(_cosine_similarity([0, 0], [1, 1]), 0.0)


class TestTypesCompatible(unittest.TestCase):
    def test_int_compat(self):
        self.assertTrue(_types_compatible("INTEGER", "BIGINT"))

    def test_text_compat(self):
        self.assertTrue(_types_compatible("TEXT", "VARCHAR(100)"))

    def test_incompatible(self):
        self.assertFalse(_types_compatible("INTEGER", "TEXT"))

    def test_same_type(self):
        self.assertTrue(_types_compatible("TEXT", "TEXT"))


class TestFindEdge(unittest.TestCase):
    def test_find_existing(self):
        edges = [{"from": "a", "to": "b", "join_keys": [], "type": "explicit_fk"}]
        result = _find_edge(edges, "a", "b")
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "explicit_fk")

    def test_find_reverse(self):
        edges = [{"from": "b", "to": "a", "join_keys": [], "type": "explicit_fk"}]
        result = _find_edge(edges, "a", "b")
        self.assertIsNotNone(result)

    def test_not_found(self):
        edges = [{"from": "a", "to": "b", "join_keys": [], "type": "explicit_fk"}]
        result = _find_edge(edges, "c", "d")
        self.assertIsNone(result)


# ============================================================================
# Stage 1 测试：显式 FK
# ============================================================================

class TestStage1ExplicitFK(unittest.TestCase):
    def _make_builder(self):
        mock_connector = MagicMock()
        mock_vector_store = MagicMock()
        return SchemaGraphBuilder(
            db_connector=mock_connector,
            vector_store=mock_vector_store,
        )

    def test_single_fk(self):
        builder = self._make_builder()
        all_schemas = {
            "orders": {
                "columns": [
                    {"name": "id", "type": "INTEGER"},
                    {"name": "customer_id", "type": "INTEGER"},
                ],
                "foreign_keys": [
                    {"column": "customer_id", "references_table": "customers", "references_column": "id"}
                ],
            },
            "customers": {
                "columns": [
                    {"name": "id", "type": "INTEGER"},
                    {"name": "name", "type": "TEXT"},
                ],
                "foreign_keys": [],
            },
        }

        edges, connected = builder._stage1_explicit_fk(all_schemas)

        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["type"], "explicit_fk")
        self.assertEqual(edges[0]["join_keys"], [["orders.customer_id", "customers.id"]])
        self.assertIn(_pair_key("orders", "customers"), connected)

    def test_multiple_fks_same_table(self):
        """两个表之间有多个 FK 应合并为一个 edge，多个 join_keys"""
        builder = self._make_builder()
        all_schemas = {
            "orders": {
                "columns": [
                    {"name": "id", "type": "INTEGER"},
                    {"name": "billing_addr_id", "type": "INTEGER"},
                    {"name": "shipping_addr_id", "type": "INTEGER"},
                ],
                "foreign_keys": [
                    {"column": "billing_addr_id", "references_table": "addresses", "references_column": "id"},
                    {"column": "shipping_addr_id", "references_table": "addresses", "references_column": "id"},
                ],
            },
            "addresses": {
                "columns": [{"name": "id", "type": "INTEGER"}],
                "foreign_keys": [],
            },
        }

        edges, connected = builder._stage1_explicit_fk(all_schemas)

        self.assertEqual(len(edges), 1)
        self.assertEqual(len(edges[0]["join_keys"]), 2)

    def test_no_fk(self):
        builder = self._make_builder()
        all_schemas = {
            "table_a": {"columns": [{"name": "id"}], "foreign_keys": []},
            "table_b": {"columns": [{"name": "id"}], "foreign_keys": []},
        }

        edges, connected = builder._stage1_explicit_fk(all_schemas)
        self.assertEqual(len(edges), 0)


# ============================================================================
# Stage 2 测试：向量相似度匹配 + 命中率检测
# ============================================================================

class TestStage2VectorSimilarity(unittest.TestCase):
    def _make_builder_with_mocks(self):
        mock_connector = MagicMock()
        mock_vector_store = MagicMock()
        builder = SchemaGraphBuilder(
            db_connector=mock_connector,
            vector_store=mock_vector_store,
            hit_rate_threshold=0.5,
            top_similar_pairs=3,
            sample_size=20,
        )
        return builder, mock_connector, mock_vector_store

    def test_no_vector_store_returns_empty(self):
        builder = SchemaGraphBuilder(db_connector=MagicMock())
        result = builder._stage2_vector_similarity("db", [], {}, set())
        self.assertEqual(result, [])

    def test_hit_rate_pass(self):
        """命中率达标 → 通过验证"""
        builder, mock_connector, _ = self._make_builder_with_mocks()

        # _get_column_samples 返回表 A 的样本值
        # _check_hit_rate 返回命中数
        mock_connector.execute_query.side_effect = [
            (True, [("val1",), ("val2",), ("val3",)], ""),  # _get_column_samples
            (True, [("val1",), ("val2",)], ""),              # _check_hit_rate 的 IN 查询
        ]

        col_a = {
            "metadata": {
                "table_name": "table_a",
                "original_column_name": "col_a",
                "data_type": "TEXT",
            },
        }
        col_b = {
            "metadata": {
                "table_name": "table_b",
                "original_column_name": "col_b",
                "data_type": "TEXT",
            },
        }

        result = builder._verify_value_overlap(
            [(col_a, col_b, 0.9)], {}
        )

        # val1 和 val2 在 table_b 中存在 → hit_rate = 2/3 = 0.667 >= 0.5 → 通过
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], ["table_a.col_a", "table_b.col_b"])

    def test_hit_rate_below_threshold(self):
        """命中率不达标 → 不通过"""
        builder, mock_connector, _ = self._make_builder_with_mocks()

        mock_connector.execute_query.side_effect = [
            (True, [("val1",), ("val2",), ("val3",)], ""),  # _get_column_samples
            (True, [], ""),  # _check_hit_rate: 无匹配
        ]

        col_a = {
            "metadata": {
                "table_name": "table_a",
                "original_column_name": "col_a",
                "data_type": "TEXT",
            },
        }
        col_b = {
            "metadata": {
                "table_name": "table_b",
                "original_column_name": "col_b",
                "data_type": "TEXT",
            },
        }

        result = builder._verify_value_overlap(
            [(col_a, col_b, 0.9)], {}
        )

        # hit_rate = 0/3 = 0 < 0.5 → 不通过
        self.assertEqual(len(result), 0)

    def test_incompatible_types_filtered(self):
        builder, _, _ = self._make_builder_with_mocks()

        col_a = {
            "metadata": {
                "table_name": "table_a",
                "original_column_name": "col_a",
                "data_type": "INTEGER",
            },
        }
        col_b = {
            "metadata": {
                "table_name": "table_b",
                "original_column_name": "col_b",
                "data_type": "TEXT",
            },
        }

        result = builder._verify_value_overlap(
            [(col_a, col_b, 0.9)], {}
        )

        self.assertEqual(len(result), 0)

    def test_multiple_join_keys(self):
        """两个表间多个列对通过验证"""
        builder, mock_connector, _ = self._make_builder_with_mocks()

        # 每个列对：1 次 _get_column_samples + 1 次 _check_hit_rate
        mock_connector.execute_query.side_effect = [
            (True, [("id1",), ("id2",)], ""),    # col_a.school_id: _get_column_samples
            (True, [("id1",), ("id2",)], ""),    # col_a.school_id: _check_hit_rate
            (True, [("name_a",), ("name_b",)], ""),  # col_a.school_name: _get_column_samples
            (True, [("name_a",), ("name_b",)], ""),  # col_a.school_name: _check_hit_rate
        ]

        col_a_id = {
            "metadata": {
                "table_name": "table_a",
                "original_column_name": "school_id",
                "data_type": "INTEGER",
            },
        }
        col_b_id = {
            "metadata": {
                "table_name": "table_b",
                "original_column_name": "school_id",
                "data_type": "INTEGER",
            },
        }
        col_a_name = {
            "metadata": {
                "table_name": "table_a",
                "original_column_name": "school_name",
                "data_type": "TEXT",
            },
        }
        col_b_name = {
            "metadata": {
                "table_name": "table_b",
                "original_column_name": "school_name",
                "data_type": "TEXT",
            },
        }

        result = builder._verify_value_overlap(
            [(col_a_id, col_b_id, 0.9), (col_a_name, col_b_name, 0.85)], {}
        )

        self.assertEqual(len(result), 2)


# ============================================================================
# JOIN 路径提取测试
# ============================================================================

class TestExtractJoinPaths(unittest.TestCase):
    def _make_graph(self):
        return {
            "california_schools": {
                "nodes": {
                    "schools": {"columns": ["CDSCode", "School"]},
                    "satscores": {"columns": ["cds", "AvgScrRead"]},
                    "frpm": {"columns": ["CDSCode", "SchoolName"]},
                },
                "edges": [
                    {
                        "from": "satscores",
                        "to": "schools",
                        "join_keys": [["satscores.cds", "schools.CDSCode"]],
                        "type": "explicit_fk",
                    },
                    {
                        "from": "frpm",
                        "to": "schools",
                        "join_keys": [
                            ["frpm.CDSCode", "schools.CDSCode"],
                            ["frpm.SchoolName", "schools.School"],
                        ],
                        "type": "vector_similarity",
                    },
                ],
            }
        }

    def test_direct_join(self):
        graph = self._make_graph()
        result = extract_join_paths(graph, ["satscores", "schools"])
        self.assertEqual(len(result["edges"]), 1)
        self.assertEqual(result["edges"][0]["type"], "explicit_fk")
        # 直接连接，无桥接表
        self.assertEqual(result["bridge_tables"], [])

    def test_two_hop_join_with_bridge_table(self):
        graph = self._make_graph()
        result = extract_join_paths(graph, ["satscores", "frpm"])
        # satscores → schools → frpm，应找到 2 条边
        self.assertEqual(len(result["edges"]), 2)
        types = {e["type"] for e in result["edges"]}
        self.assertIn("explicit_fk", types)
        self.assertIn("vector_similarity", types)
        # schools 是桥接表
        self.assertIn("schools", result["bridge_tables"])

    def test_single_table(self):
        graph = self._make_graph()
        result = extract_join_paths(graph, ["schools"])
        self.assertEqual(len(result["edges"]), 0)
        self.assertEqual(result["bridge_tables"], [])

    def test_no_connection(self):
        graph = {
            "db": {
                "nodes": {"a": {"columns": []}, "b": {"columns": []}},
                "edges": [],
            }
        }
        result = extract_join_paths(graph, ["a", "b"])
        self.assertEqual(len(result["edges"]), 0)

    def test_empty_input(self):
        result = extract_join_paths({}, ["a"])
        self.assertEqual(result["edges"], [])

        result = extract_join_paths({"db": {"nodes": {}, "edges": []}}, [])
        self.assertEqual(result["edges"], [])


# ============================================================================
# Prompt 格式化测试
# ============================================================================

class TestFormatJoinPathsForPrompt(unittest.TestCase):
    def test_single_edge(self):
        join_paths_result = {
            "edges": [
                {
                    "from": "satscores",
                    "to": "schools",
                    "join_keys": [["satscores.cds", "schools.CDSCode"]],
                    "type": "explicit_fk",
                }
            ],
            "bridge_tables": [],
        }
        text = format_join_paths_for_prompt(join_paths_result)
        self.assertIn("satscores ←[satscores.cds = schools.CDSCode]→ schools", text)
        self.assertIn("satscores JOIN schools ON satscores.cds = schools.CDSCode", text)

    def test_multiple_join_keys(self):
        join_paths_result = {
            "edges": [
                {
                    "from": "frpm",
                    "to": "schools",
                    "join_keys": [
                        ["frpm.CDSCode", "schools.CDSCode"],
                        ["frpm.SchoolName", "schools.School"],
                    ],
                    "type": "vector_similarity",
                }
            ],
            "bridge_tables": [],
        }
        text = format_join_paths_for_prompt(join_paths_result)
        self.assertIn("frpm.CDSCode = schools.CDSCode", text)
        self.assertIn("frpm.SchoolName = schools.School", text)
        self.assertIn("AND", text)

    def test_bridge_tables_shown(self):
        join_paths_result = {
            "edges": [
                {
                    "from": "satscores",
                    "to": "schools",
                    "join_keys": [["satscores.cds", "schools.CDSCode"]],
                    "type": "explicit_fk",
                },
            ],
            "bridge_tables": ["schools"],
        }
        text = format_join_paths_for_prompt(join_paths_result)
        self.assertIn("桥接表: schools", text)

    def test_empty_input(self):
        self.assertEqual(format_join_paths_for_prompt({}), "")
        self.assertEqual(format_join_paths_for_prompt(None), "")
        self.assertEqual(format_join_paths_for_prompt({"edges": [], "bridge_tables": []}), "")


# ============================================================================
# enrich_schema_with_join_paths 测试（relocate-join-path-injection）
# JOIN 路径注入 + 桥接表 M-Schema 补全，从 IR 阶段迁移到 SS→CG 之间
# ============================================================================

class TestEnrichSchemaWithJoinPaths(unittest.TestCase):
    """测试 enrich_schema_with_join_paths 纯函数。"""

    def _make_graph(self):
        return {
            "california_schools": {
                "nodes": {
                    "schools": {"columns": ["CDSCode", "School"]},
                    "satscores": {"columns": ["cds", "AvgScrRead"]},
                    "frpm": {"columns": ["CDSCode", "SchoolName"]},
                },
                "edges": [
                    {
                        "from": "satscores",
                        "to": "schools",
                        "join_keys": [["satscores.cds", "schools.CDSCode"]],
                        "type": "explicit_fk",
                    },
                    {
                        "from": "frpm",
                        "to": "schools",
                        "join_keys": [["frpm.CDSCode", "schools.CDSCode"]],
                        "type": "vector_similarity",
                    },
                ],
            }
        }

    def _write_graph(self, tmpdir, db_id="california_schools"):
        """在 tmpdir/preprocessed/schema_graphs/{db_id}.json 写图文件。"""
        graph_dir = Path(tmpdir) / "preprocessed" / "schema_graphs"
        graph_dir.mkdir(parents=True, exist_ok=True)
        graph_path = graph_dir / f"{db_id}.json"
        SchemaGraphBuilder.save(self._make_graph(), str(graph_path))
        return str(tmpdir)

    def _mock_vector_store(self, table_name="schools"):
        """mock 向量库：对指定表返回其列。"""
        mock_vs = MagicMock()
        mock_vs.query.return_value = [
            {
                "metadata": {
                    "database": "california_schools",
                    "table_name": table_name,
                    "original_column_name": "CDSCode",
                    "data_type": "TEXT",
                    "description": "学校代码",
                    "sample_values": ["12345"],
                    "is_primary_key": True,
                },
                "document": f"{table_name} | cdscode | ",
            }
        ]
        return mock_vs

    def _mock_vectorizer(self):
        mock_vec = MagicMock()
        mock_vec.model = MagicMock()  # 非空，通过有效性守卫
        mock_vec.embed_texts.return_value = {"dense": [[0.1] * 1024]}
        return mock_vec

    def _mschema_table(self, name):
        from src.schema_selection.schema_selector import MSchemaTable, MSchemaColumn
        return MSchemaTable(name=name, columns=[MSchemaColumn(name="x", data_type="TEXT")])

    def test_bridge_table_enriched_as_mschema(self):
        """桥接表应被补成 MSchemaTable 并加入 selected_schema，且 join_paths_text 非空。"""
        from src.preprocessing.schema_graph_builder import enrich_schema_with_join_paths

        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = self._write_graph(tmpdir)
            selected = [self._mschema_table("satscores"), self._mschema_table("frpm")]
            result_schema, join_text = enrich_schema_with_join_paths(
                selected_schema=selected,
                database_filter="california_schools",
                vector_store=self._mock_vector_store("schools"),
                vectorizer=self._mock_vectorizer(),
                data_dir=data_dir,
            )
            # schools 是 satscores→schools→frpm 的桥接表，应被补进 schema
            self.assertTrue(any(getattr(t, "name", "") == "schools" for t in result_schema))
            # 桥接表的列 CDSCode 应存在
            schools_tbl = next(t for t in result_schema if getattr(t, "name", "") == "schools")
            self.assertTrue(any(c.name == "CDSCode" for c in schools_tbl.columns))
            # join_paths_text 非空
            self.assertTrue(join_text)
            self.assertIn("JOIN", join_text)

    def test_bridge_table_not_duplicated(self):
        """已存在的桥接表不应重复添加。"""
        from src.preprocessing.schema_graph_builder import enrich_schema_with_join_paths

        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = self._write_graph(tmpdir)
            # schools 已在 schema 中，又同时是 satscores↔frpm 的桥接表 → 不应重复
            selected = [
                self._mschema_table("satscores"),
                self._mschema_table("schools"),
                self._mschema_table("frpm"),
            ]
            result_schema, join_text = enrich_schema_with_join_paths(
                selected_schema=selected,
                database_filter="california_schools",
                vector_store=self._mock_vector_store(),
                vectorizer=self._mock_vectorizer(),
                data_dir=data_dir,
            )
            # schools 只出现一次
            schools_count = sum(1 for t in result_schema if getattr(t, "name", "") == "schools")
            self.assertEqual(schools_count, 1)

    def test_no_database_filter_degrades(self):
        """database_filter 为空 → schema 原样、join_paths_text 为空。"""
        from src.preprocessing.schema_graph_builder import enrich_schema_with_join_paths

        selected = [self._mschema_table("a"), self._mschema_table("b")]
        result_schema, join_text = enrich_schema_with_join_paths(
            selected_schema=selected,
            database_filter=None,
            vector_store=None,
            vectorizer=None,
            data_dir="/nonexistent",
        )
        self.assertEqual(len(result_schema), 2)
        self.assertEqual(join_text, "")

    def test_single_table_degrades(self):
        """表数 < 2 → schema 原样、join_paths_text 为空。"""
        from src.preprocessing.schema_graph_builder import enrich_schema_with_join_paths

        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = self._write_graph(tmpdir)
            selected = [self._mschema_table("schools")]
            result_schema, join_text = enrich_schema_with_join_paths(
                selected_schema=selected,
                database_filter="california_schools",
                vector_store=None,
                vectorizer=None,
                data_dir=data_dir,
            )
            self.assertEqual(len(result_schema), 1)
            self.assertEqual(join_text, "")

    def test_graph_not_found_degrades(self):
        """关联图不存在 → schema 原样、join_paths_text 为空。"""
        from src.preprocessing.schema_graph_builder import enrich_schema_with_join_paths

        selected = [self._mschema_table("a"), self._mschema_table("b")]
        result_schema, join_text = enrich_schema_with_join_paths(
            selected_schema=selected,
            database_filter="nonexistent_db",
            vector_store=None,
            vectorizer=None,
            data_dir=tempfile.gettempdir(),
        )
        self.assertEqual(len(result_schema), 2)
        self.assertEqual(join_text, "")

    def test_exception_fallback(self):
        """函数内部异常应兜底：schema 原样、join_paths_text 为空，不抛异常。"""
        from src.preprocessing.schema_graph_builder import enrich_schema_with_join_paths

        # 给一个会在 extract_join_paths 前就抛异常的 vectorizer（model 非空但 embed 抛异常），
        # 但桥接表补全在 extract 之后；这里用破坏的 graph load 触发异常路径。
        with tempfile.TemporaryDirectory() as tmpdir:
            graph_dir = Path(tmpdir) / "preprocessed" / "schema_graphs"
            graph_dir.mkdir(parents=True, exist_ok=True)
            (graph_dir / "bad.json").write_text("not a json")  # 损坏的图文件
            selected = [self._mschema_table("a"), self._mschema_table("b")]
            result_schema, join_text = enrich_schema_with_join_paths(
                selected_schema=selected,
                database_filter="bad",
                vector_store=None,
                vectorizer=None,
                data_dir=tmpdir,
            )
            self.assertEqual(len(result_schema), 2)
            self.assertEqual(join_text, "")


# ============================================================================
# 持久化测试
# ============================================================================

class TestPersistence(unittest.TestCase):
    def test_save_and_load(self):
        graph = {
            "test_db": {
                "nodes": {"table_a": {"columns": ["id"]}},
                "edges": [
                    {
                        "from": "table_a",
                        "to": "table_b",
                        "join_keys": [["table_a.id", "table_b.id"]],
                        "type": "explicit_fk",
                    }
                ],
            }
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test_db.json")
            SchemaGraphBuilder.save(graph, path)
            loaded = SchemaGraphBuilder.load(path)

        self.assertEqual(loaded, graph)


# ============================================================================
# Full build 集成测试（Mock）
# ============================================================================

class TestFullBuild(unittest.TestCase):
    def test_build_with_mocks(self):
        mock_connector = MagicMock()
        mock_connector.get_all_schemas.return_value = {
            "orders": {
                "columns": [
                    {"name": "id", "type": "INTEGER"},
                    {"name": "customer_id", "type": "INTEGER"},
                ],
                "foreign_keys": [
                    {"column": "customer_id", "references_table": "customers", "references_column": "id"}
                ],
            },
            "customers": {
                "columns": [
                    {"name": "id", "type": "INTEGER"},
                    {"name": "name", "type": "TEXT"},
                ],
                "foreign_keys": [],
            },
        }

        # Mock vector store: get 返回空结果
        mock_vs = MagicMock()
        mock_vs.collection.get.return_value = {"ids": [], "metadatas": [], "documents": [], "embeddings": []}

        builder = SchemaGraphBuilder(
            db_connector=mock_connector,
            vector_store=mock_vs,
        )

        graph = builder.build("test_db")

        self.assertIn("test_db", graph)
        self.assertIn("nodes", graph["test_db"])
        self.assertIn("edges", graph["test_db"])
        # 至少有 1 条显式 FK 边
        self.assertGreaterEqual(len(graph["test_db"]["edges"]), 1)
        self.assertEqual(graph["test_db"]["edges"][0]["type"], "explicit_fk")


if __name__ == "__main__":
    unittest.main()

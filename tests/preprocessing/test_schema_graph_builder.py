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
# 桥接表 M-Schema 补充测试
# ============================================================================

class TestBridgeTableMSchema(unittest.TestCase):
    def test_add_bridge_tables(self):
        """测试桥接表列从向量库补充到 RetrievedContext"""
        from src.retrieval.information_retrieval import (
            InformationRetrieval, RetrievedContext, RetrievedItem,
        )

        mock_vs = MagicMock()
        mock_vs.query.return_value = [
            {
                "id": "db.schools.CDSCode",
                "metadata": {
                    "database": "db",
                    "table_name": "schools",
                    "original_column_name": "CDSCode",
                    "column_name": "CDSCode",
                },
                "document": "schools | cdscode | ",
                "distance": 0.3,
            },
        ]

        mock_vectorizer = MagicMock()
        mock_vectorizer.embed_texts.return_value = {"dense": [[0.1] * 1024]}

        ir = InformationRetrieval(vector_store=mock_vs)
        ir._vectorizer = mock_vectorizer

        ctx = RetrievedContext(
            tables=[RetrievedItem(item_type="table", name="satscores")],
            columns=[RetrievedItem(item_type="column", name="cds", table_name="satscores")],
            values=[],
        )

        ir._add_bridge_tables(ctx, ["schools"], "db")

        # schools 表应被添加
        self.assertTrue(any(t.name == "schools" for t in ctx.tables))
        # schools.CDSCode 列应被添加
        self.assertTrue(any(c.name == "CDSCode" and c.table_name == "schools" for c in ctx.columns))

    def test_bridge_table_not_duplicated(self):
        """已存在的表不应重复添加"""
        from src.retrieval.information_retrieval import (
            InformationRetrieval, RetrievedContext, RetrievedItem,
        )

        mock_vs = MagicMock()
        ir = InformationRetrieval(vector_store=mock_vs)

        ctx = RetrievedContext(
            tables=[RetrievedItem(item_type="table", name="schools")],
            columns=[RetrievedItem(item_type="column", name="CDSCode", table_name="schools")],
            values=[],
        )

        ir._add_bridge_tables(ctx, ["schools"], "db")

        # schools 只出现一次
        self.assertEqual(len([t for t in ctx.tables if t.name == "schools"]), 1)
        self.assertEqual(len([c for c in ctx.columns if c.table_name == "schools"]), 1)


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

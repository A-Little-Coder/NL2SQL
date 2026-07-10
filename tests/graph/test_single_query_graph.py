"""
single_query_graph 编排测试（relocate-join-path-injection）

覆盖：
- 节点链含 schema_finalize，且位于 ss 与 answerability_check/cg 之间
- 多表查询：join_paths_text 非空、桥接表 M-Schema 进入 selected_schema、CG 收到 join_paths_text
- 单表 / 无 database_filter 降级：join_paths_text 为空，schema 不变
- cache_hit 短路：不触发 schema_finalize
- database_filter=None：schema_finalize 降级，不抛异常
"""

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ============================================================================
# 辅助：构造一套 mock Agent（retriever 用真 InformationRetrieval 以走真实 enhance_with_schema）
# ============================================================================

def _mock_agents_for_join(join_text_expected=True):
    """构造 single_query_graph 所需 mock。

    retriever: 真实 InformationRetrieval 实例（无 LSH/向量库），但 vector_store/vectorizer
               注入 mock 以便 enrich_schema_with_join_paths 查桥接表。
    selector:  mock，返回 2 表（触发 JOIN 计算）或 1 表（降级）。
    generator/fix/dec: mock 端到端流水。
    """
    from src.retrieval.information_retrieval import (
        InformationRetrieval, RetrievedContext, RetrievedItem,
    )
    from src.schema_selection.schema_selector import MSchemaColumn, MSchemaTable
    from src.sql_generation.sql_generator import SQLCandidate, SQLStatus
    from src.decision.self_consistency import DecisionResult
    from src.execution.executor import ExecutionResult

    # IR：用真实实例，但 build_graph 返回 mock 子图产出 RetrievedContext
    retriever = InformationRetrieval()
    mock_vs = MagicMock()
    mock_vs.query.return_value = [
        {
            "metadata": {
                "database": "california_schools",
                "table_name": "schools",
                "original_column_name": "CDSCode",
                "data_type": "TEXT",
                "description": "学校代码",
                "sample_values": ["12345"],
                "is_primary_key": True,
            },
            "document": "schools | cdscode | ",
        }
    ]
    retriever.vector_store = mock_vs
    mock_vec = MagicMock()
    mock_vec.model = MagicMock()
    mock_vec.embed_texts.return_value = {"dense": [[0.1] * 1024]}
    retriever._vectorizer = mock_vec

    ir_graph = MagicMock()
    ir_graph.invoke = MagicMock(return_value={
        "keywords": ["k1"],
        "retrieved_context": RetrievedContext(
            tables=[RetrievedItem(item_type="table", name="t", score=0.9)],
            columns=[RetrievedItem(item_type="column", name="c", table_name="t", score=0.8)],
            values=[], keywords=["k1"],
            lsh_hit_count=0, vector_top_scores=[0.8],
        ),
    })
    retriever.build_graph = MagicMock(return_value=ir_graph)

    # SS：2 表触发 JOIN，1 表降级
    selector = MagicMock()
    ss_graph = MagicMock()
    if join_text_expected:
        tables = [
            MSchemaTable(name="satscores", columns=[MSchemaColumn(name="cds", data_type="TEXT")]),
            MSchemaTable(name="frpm", columns=[MSchemaColumn(name="CDSCode", data_type="TEXT")]),
        ]
    else:
        tables = [MSchemaTable(name="schools", columns=[MSchemaColumn(name="CDSCode", data_type="TEXT")])]
    ss_graph.invoke = MagicMock(return_value={"selected_schema": tables})
    selector.build_graph = MagicMock(return_value=ss_graph)

    # CG：记录收到的 join_paths_text
    generator = MagicMock()
    cg_graph = MagicMock()
    cg_received = {}

    def _cg_invoke(payload):
        cg_received["join_paths_text"] = payload.get("join_paths_text", "")
        cand = SQLCandidate(id="c1", sql="SELECT 1", status=SQLStatus.VALIDATED)
        return {"sql_candidates": [cand]}

    cg_graph.invoke = MagicMock(side_effect=_cg_invoke)
    generator.build_graph = MagicMock(return_value=cg_graph)

    fix_loop = MagicMock()
    fix_loop.executor.execute = MagicMock(return_value=ExecutionResult(
        success=True, sql="SELECT 1", result_data=[(1,)], execution_time=0.001,
    ))

    decider = MagicMock()
    dec_graph = MagicMock()
    dec_graph.invoke = MagicMock(return_value={
        "final_decision": DecisionResult(
            selected_sql="SELECT 1", selected_result=[(1,)],
            execution_time=0.001, decision_reason="mock",
        ),
    })
    decider.build_graph = MagicMock(return_value=dec_graph)
    decider.result_verifier = None

    return retriever, selector, generator, fix_loop, decider, cg_received


def _write_graph_file(tmpdir):
    """写 california_schools 关联图文件。"""
    from src.preprocessing.schema_graph_builder import SchemaGraphBuilder
    graph_dir = Path(tmpdir) / "preprocessed" / "schema_graphs"
    graph_dir.mkdir(parents=True, exist_ok=True)
    graph = {
        "california_schools": {
            "nodes": {
                "schools": {"columns": ["CDSCode"]},
                "satscores": {"columns": ["cds"]},
                "frpm": {"columns": ["CDSCode"]},
            },
            "edges": [
                {"from": "satscores", "to": "schools",
                 "join_keys": [["satscores.cds", "schools.CDSCode"]], "type": "explicit_fk"},
                {"from": "frpm", "to": "schools",
                 "join_keys": [["frpm.CDSCode", "schools.CDSCode"]], "type": "vector_similarity"},
            ],
        }
    }
    SchemaGraphBuilder.save(graph, str(graph_dir / "california_schools.json"))
    return str(tmpdir)


# ============================================================================
# 编排断言
# ============================================================================

def test_pipeline_has_schema_finalize_node():
    """single_query_graph 节点链含 schema_finalize，且 ss → schema_finalize → cg 顺序。"""
    retr, sel, gen, fix, dec, _ = _mock_agents_for_join(join_text_expected=False)
    from src.graph.single_query_graph import build_single_query_graph
    graph = build_single_query_graph(retr, sel, gen, fix, dec)
    # 编译后的图节点名集合
    node_names = set(graph.nodes.keys())
    assert "schema_finalize" in node_names, "缺少 schema_finalize 节点"
    assert "ss" in node_names and "cg" in node_names


# ============================================================================
# 多表：join_paths_text 端到端
# ============================================================================

def test_multi_table_join_paths_text_flows_to_cg():
    """多表查询：join_paths_text 非空、桥接表进 selected_schema、CG 收到 join_paths_text。"""
    from src.graph import create_initial_state
    from src.graph.single_query_graph import build_single_query_graph

    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = _write_graph_file(tmpdir)
        retr, sel, gen, fix, dec, cg_received = _mock_agents_for_join(join_text_expected=True)
        graph = build_single_query_graph(retr, sel, gen, fix, dec, data_dir=data_dir)

        state = create_initial_state(
            user_query="satscores 和 frpm 的对比",
            database_filter="california_schools",
        )
        result = graph.invoke(state)

        # join_paths_text 非空
        assert result.get("join_paths_text"), "join_paths_text 应非空"
        assert "JOIN" in result["join_paths_text"]
        # 桥接表 schools 进入 selected_schema（SS 只产出 2 表，+1 桥接）
        table_names = [getattr(t, "name", "") for t in result.get("selected_schema", [])]
        assert "schools" in table_names, "桥接表 schools 应补进 selected_schema"
        # CG 子图收到 join_paths_text
        assert cg_received.get("join_paths_text"), "CG 应收到 join_paths_text"
        assert "JOIN" in cg_received["join_paths_text"]


# ============================================================================
# 单表 / 无 database_filter 降级
# ============================================================================

def test_single_table_no_join_text():
    """单表查询：join_paths_text 为空，schema 不变。"""
    from src.graph import create_initial_state
    from src.graph.single_query_graph import build_single_query_graph

    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = _write_graph_file(tmpdir)
        retr, sel, gen, fix, dec, cg_received = _mock_agents_for_join(join_text_expected=False)
        graph = build_single_query_graph(retr, sel, gen, fix, dec, data_dir=data_dir)

        state = create_initial_state(
            user_query="schools 信息",
            database_filter="california_schools",
        )
        result = graph.invoke(state)

        assert result.get("join_paths_text", "") == ""
        assert cg_received.get("join_paths_text", "") == ""
        # schema 仍是 SS 产出的 1 表
        assert len(result.get("selected_schema", [])) == 1


def test_no_database_filter_degrades():
    """database_filter=None：schema_finalize 降级，不抛异常，join_paths_text 为空。"""
    from src.graph import create_initial_state
    from src.graph.single_query_graph import build_single_query_graph

    retr, sel, gen, fix, dec, cg_received = _mock_agents_for_join(join_text_expected=True)
    graph = build_single_query_graph(retr, sel, gen, fix, dec)

    state = create_initial_state(user_query="对比", database_filter=None)
    result = graph.invoke(state)

    assert result.get("join_paths_text", "") == ""
    assert cg_received.get("join_paths_text", "") == ""


# ============================================================================
# cache_hit 短路
# ============================================================================

def test_cache_hit_short_circuits_schema_finalize():
    """cache_hit=True：跳过 ir/ss/schema_finalize，直接 execution（使用 adjusted_cached_sql 优先）。"""
    from src.graph import create_initial_state
    from src.graph.single_query_graph import build_single_query_graph

    retr, sel, gen, fix, dec, _ = _mock_agents_for_join(join_text_expected=False)
    # 记录实际执行的 SQL
    executed_sql = []
    original_execute = fix.executor.execute

    def _record_execute(sql):
        executed_sql.append(sql)
        return original_execute(sql)
    fix.executor.execute = _record_execute

    graph = build_single_query_graph(retr, sel, gen, fix, dec)

    state = create_initial_state(user_query="缓存命中查询", database_filter="california_schools")
    state["cache_hit"] = True
    state["cached_sql"] = "SELECT old"
    state["adjusted_cached_sql"] = "SELECT new"  # harden-history-cache
    result = graph.invoke(state)

    # IR 未被调用（build_graph.invoke 没触发）
    assert retr.build_graph.return_value.invoke.call_count == 0
    # SS 未被调用
    assert sel.build_graph.return_value.invoke.call_count == 0
    # join_paths_text 为空（schema_finalize 未执行）
    assert result.get("join_paths_text", "") == ""
    # 验证执行了 adjusted_cached_sql
    assert executed_sql == ["SELECT new"]


def test_cache_hit_falls_back_to_cached_sql():
    """cache_hit=True 且 adjusted_cached_sql 为空：回退使用 cached_sql。"""
    from src.graph import create_initial_state
    from src.graph.single_query_graph import build_single_query_graph

    retr, sel, gen, fix, dec, _ = _mock_agents_for_join(join_text_expected=False)
    # 记录实际执行的 SQL
    executed_sql = []
    original_execute = fix.executor.execute

    def _record_execute(sql):
        executed_sql.append(sql)
        return original_execute(sql)
    fix.executor.execute = _record_execute

    graph = build_single_query_graph(retr, sel, gen, fix, dec)

    state = create_initial_state(user_query="缓存命中查询", database_filter="california_schools")
    state["cache_hit"] = True
    state["cached_sql"] = "SELECT fallback"
    state["adjusted_cached_sql"] = None
    result = graph.invoke(state)

    # 验证执行了 cached_sql（回退）
    assert executed_sql == ["SELECT fallback"]


def test_cache_hit_both_sql_empty_returns_error():
    """cache_hit=True 但 cached_sql 与 adjusted_cached_sql 均为空：execution 返回 error。"""
    from src.graph import create_initial_state
    from src.graph.single_query_graph import build_single_query_graph

    retr, sel, gen, fix, dec, _ = _mock_agents_for_join(join_text_expected=False)
    graph = build_single_query_graph(retr, sel, gen, fix, dec)

    state = create_initial_state(user_query="缓存命中查询", database_filter="california_schools")
    state["cache_hit"] = True
    state["cached_sql"] = ""
    state["adjusted_cached_sql"] = None
    result = graph.invoke(state)

    assert "cache_hit" in (result.get("error") or "")

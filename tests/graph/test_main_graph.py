"""
§18.10 主图端到端测试（Mock 所有 Agent）

验证：
- 主图能从 START 跑到 END
- 状态在节点间正确流动（IR → SS → CG → Execution → Decision）
- 条件边：SS 无 schema 时短路、CG 无候选时短路
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def _build_complete_mocks(success_path=True):
    """构造一套主图所需的全部 Mock Agent，返回 (retriever, selector, gen, fix, dec)"""
    from src.decision.self_consistency import DecisionResult
    from src.execution.executor import ExecutionResult
    from src.retrieval.information_retrieval import RetrievedContext, RetrievedItem
    from src.schema_selection.schema_selector import MSchemaColumn, MSchemaTable
    from src.sql_generation.sql_generator import SQLCandidate, SQLStatus

    # --- IR：build_graph 返回一个 callable graph，invoke 返回固定 dict ---
    retriever = MagicMock()
    ir_graph = MagicMock()
    ir_graph.invoke = MagicMock(return_value={
        "keywords": ["k1"],
        "retrieved_context": RetrievedContext(
            tables=[RetrievedItem(item_type="table", name="t", score=0.9)],
            columns=[RetrievedItem(item_type="column", name="c",
                                    table_name="t", score=0.8)],
            values=[], keywords=["k1"],
            lsh_hit_count=0, vector_top_scores=[0.8],
        ),
    })
    retriever.build_graph = MagicMock(return_value=ir_graph)

    # --- SS ---
    selector = MagicMock()
    ss_graph = MagicMock()
    if success_path:
        col = MSchemaColumn(name="c", data_type="INT")
        table = MSchemaTable(name="t", columns=[col])
        ss_graph.invoke = MagicMock(return_value={"selected_schema": [table]})
    else:
        ss_graph.invoke = MagicMock(return_value={"selected_schema": []})
    selector.build_graph = MagicMock(return_value=ss_graph)

    # --- CG ---
    generator = MagicMock()
    cg_graph = MagicMock()
    cand = SQLCandidate(
        id="c1", sql="SELECT * FROM t", status=SQLStatus.VALIDATED,
    )
    cg_graph.invoke = MagicMock(return_value={"sql_candidates": [cand]})
    generator.build_graph = MagicMock(return_value=cg_graph)

    # --- Execution ---
    fix_loop = MagicMock()
    exec_graph = MagicMock()
    exec_graph.invoke = MagicMock(return_value={
        "result": ExecutionResult(
            success=True, sql="SELECT * FROM t",
            result_data=[(1,)], execution_time=0.001,
        ),
        "fix_history": [],
    })
    fix_loop.build_graph = MagicMock(return_value=exec_graph)

    # --- Decision ---
    decider = MagicMock()
    dec_graph = MagicMock()
    dec_graph.invoke = MagicMock(return_value={
        "final_decision": DecisionResult(
            selected_sql="SELECT * FROM t",
            selected_result=[(1,)],
            execution_time=0.001,
            decision_reason="mock",
        ),
    })
    decider.build_graph = MagicMock(return_value=dec_graph)

    return retriever, selector, generator, fix_loop, decider


# ============================================================================
# 主图端到端
# ============================================================================

def test_main_graph_happy_path():
    from src.graph import build_main_graph, create_initial_state

    retr, sel, gen, fix, dec = _build_complete_mocks(success_path=True)
    graph = build_main_graph(retr, sel, gen, fix, dec)

    state = create_initial_state(user_query="去年销售额")
    result = graph.invoke(state)

    # 各节点状态都正确落位
    assert result["keywords"] == ["k1"]
    assert result["retrieved_context"] is not None
    assert len(result["selected_schema"]) == 1
    assert len(result["sql_candidates"]) == 1
    assert result["final_sql"] == "SELECT * FROM t"
    assert result["final_result"] == [(1,)]
    # trace_log 记录了各节点
    log = " ".join(result.get("trace_log", []))
    assert "IR" in log and "SS" in log and "CG" in log
    assert "Execution" in log and "Decision" in log


def test_main_graph_short_circuit_no_schema():
    """SS 返回空 selected_schema 时，主图直接 END，不进 CG/Execution"""
    from src.graph import build_main_graph, create_initial_state

    retr, sel, gen, fix, dec = _build_complete_mocks(success_path=False)
    graph = build_main_graph(retr, sel, gen, fix, dec)

    state = create_initial_state(user_query="无解的查询")
    result = graph.invoke(state)

    assert result.get("selected_schema") == []
    # CG / Execution / Decision 节点不应被触发
    gen.build_graph.assert_not_called()
    fix.build_graph.assert_not_called()
    dec.build_graph.assert_not_called()


def test_main_graph_short_circuit_no_candidates():
    """CG 返回空候选时，主图直接 END，不进 Execution / Decision"""
    from src.graph import build_main_graph, create_initial_state

    retr, sel, gen, fix, dec = _build_complete_mocks(success_path=True)
    # 改写 CG 返回空
    cg_graph = MagicMock()
    cg_graph.invoke = MagicMock(return_value={"sql_candidates": []})
    gen.build_graph = MagicMock(return_value=cg_graph)

    graph = build_main_graph(retr, sel, gen, fix, dec)
    state = create_initial_state(user_query="生成失败")
    result = graph.invoke(state)

    assert result.get("sql_candidates") == []
    fix.build_graph.assert_not_called()
    dec.build_graph.assert_not_called()


def test_main_graph_clarification_skipped_by_default():
    """Phase 1 中 clarification 节点默认透传"""
    from src.graph import build_main_graph, create_initial_state

    retr, sel, gen, fix, dec = _build_complete_mocks(success_path=True)
    graph = build_main_graph(retr, sel, gen, fix, dec)

    state = create_initial_state(user_query="x")
    state["clarification_done"] = False  # 故意设 False
    result = graph.invoke(state)

    # 节点会重新置回 True 让流程继续
    assert result["clarification_done"] is True
    assert result["final_sql"]


def test_main_graph_state_initial_values():
    """create_initial_state 返回的 State 字段完备"""
    from src.graph import create_initial_state

    s = create_initial_state(user_query="hello", user_id="u1")
    assert s["user_query"] == "hello"
    assert s["user_id"] == "u1"
    assert s["keywords"] == []
    assert s["clarification_count"] == 0
    assert s["clarification_done"] is True
    assert s["error"] is None

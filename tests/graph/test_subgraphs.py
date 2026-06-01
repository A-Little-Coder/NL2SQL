"""
§18.11 子图独立可调用 + 状态契约测试

覆盖：
- IR 子图：使用 Mock retriever，验证 keywords/values/schema → retrieved_context 流转
- SS 子图：使用 Mock selector，验证 to_mschema → evaluate → filter 流转
- CG 子图：使用 Mock generator，验证四步骤流转
- Execution 子图：成功/失败-修复成功/失败-超限三类路径
- Decision 子图：无候选/全失败/多数/LLM 决策四类路径
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ============================================================================
# IR 子图
# ============================================================================

def test_ir_subgraph_end_to_end():
    from src.retrieval.information_retrieval import (
        InformationRetrieval,
        RetrievedItem,
    )

    ir = InformationRetrieval()
    # 打桩各方法
    ir.extract_keywords = MagicMock(return_value=["销售额", "去年"])
    ir.retrieve_values = MagicMock(return_value=[
        RetrievedItem(item_type="value", name="2023", table_name="t",
                      score=0.9, metadata={"column_name": "year"})
    ])
    ir.retrieve_schema = MagicMock(return_value={
        "tables": [RetrievedItem(item_type="table", name="t", score=0.8)],
        "columns": [RetrievedItem(item_type="column", name="amount",
                                   table_name="t", score=0.7)],
    })
    # enhance_with_schema 保持原行为（IR 已实现）

    g = ir.build_graph()
    result = g.invoke({"user_query": "去年销售额"})

    assert result["keywords"] == ["销售额", "去年"]
    ctx = result["retrieved_context"]
    assert ctx is not None
    assert len(ctx.keywords) == 2
    assert ctx.lsh_hit_count == 1
    assert len(ctx.tables) >= 1
    assert len(ctx.columns) >= 1


def test_ir_subgraph_empty_lsh():
    """LSH 未配置时 retrieve_values 返回空，流程仍能走完"""
    from src.retrieval.information_retrieval import InformationRetrieval

    ir = InformationRetrieval()
    ir.extract_keywords = MagicMock(return_value=["x"])
    ir.retrieve_values = MagicMock(return_value=[])
    ir.retrieve_schema = MagicMock(return_value={"tables": [], "columns": []})

    g = ir.build_graph()
    result = g.invoke({"user_query": "test"})
    assert result["retrieved_context"].lsh_hit_count == 0


# ============================================================================
# SS 子图
# ============================================================================

def test_ss_subgraph_end_to_end():
    from src.schema_selection.schema_selector import (
        MSchemaColumn,
        MSchemaTable,
        SchemaSelector,
    )

    ss = SchemaSelector()
    col = MSchemaColumn(name="id", data_type="INT", is_primary_key=True)
    table = MSchemaTable(name="t", columns=[col])
    ss.to_mschema = MagicMock(return_value=[table])
    ss.evaluate_column_relevance = MagicMock(return_value=[table])
    ss.filter_columns = MagicMock(return_value=[table])

    g = ss.build_graph()
    result = g.invoke({"user_query": "q", "retrieved_context": None})

    assert len(result["selected_schema"]) == 1
    ss.to_mschema.assert_called_once()
    ss.evaluate_column_relevance.assert_called_once()
    ss.filter_columns.assert_called_once()


# ============================================================================
# CG 子图
# ============================================================================

def test_cg_subgraph_no_llm_returns_empty():
    """无 llm_client 时直接返回空候选"""
    from src.sql_generation.sql_generator import SQLGenerator

    cg = SQLGenerator(llm_client=None, num_candidates=3)
    g = cg.build_graph()
    result = g.invoke({"user_query": "q", "selected_schema": []})
    assert result["sql_candidates"] == []


def test_cg_subgraph_with_mock_llm():
    """注入 Mock LLM 返回 1 个候选"""
    from src.schema_selection.schema_selector import MSchemaColumn, MSchemaTable
    from src.sql_generation.sql_generator import SQLGenerator

    mock_llm = MagicMock()
    mock_llm.chat_json = MagicMock(return_value={
        "candidates": [{"sql": "SELECT * FROM t", "reason": "test"}]
    })
    cg = SQLGenerator(llm_client=mock_llm, num_candidates=2)

    col = MSchemaColumn(name="id", data_type="INT")
    table = MSchemaTable(name="t", columns=[col])

    g = cg.build_graph()
    result = g.invoke({"user_query": "查询全部", "selected_schema": [table]})

    assert len(result["sql_candidates"]) == 1
    assert result["sql_candidates"][0].sql == "SELECT * FROM t"


# ============================================================================
# Execution 子图
# ============================================================================

def test_execution_subgraph_success_first_try():
    """首次执行成功，无需进入 fix 分支"""
    from src.execution.executor import ExecutionResult, SQLExecutor, SQLFixLoop

    executor = MagicMock(spec=SQLExecutor)
    executor.execute = MagicMock(return_value=ExecutionResult(
        success=True, sql="SELECT 1", result_data=[(1,)],
        execution_time=0.001,
    ))
    fix_loop = SQLFixLoop(executor=executor, llm_client=None, max_retries=2)
    fix_loop._try_fix = MagicMock(return_value=None)

    g = fix_loop.build_graph()
    result = g.invoke({
        "sql": "SELECT 1", "original_sql": "SELECT 1",
        "user_query": "q", "schema_text": "",
        "attempt": 0, "fix_history": [],
    })
    assert result["result"].success is True
    executor.execute.assert_called_once()
    fix_loop._try_fix.assert_not_called()


def test_execution_subgraph_fail_then_fix_success():
    """首次失败 → LLM 修复 → 重试成功"""
    from src.execution.executor import (
        ErrorType,
        ExecutionResult,
        SQLExecutor,
        SQLFixLoop,
        StructuredError,
    )

    executor = MagicMock(spec=SQLExecutor)
    err = StructuredError(
        error_type=ErrorType.SYNTAX_ERROR,
        original_message="syntax error",
    )
    executor.execute = MagicMock(side_effect=[
        ExecutionResult(success=False, sql="bad", error=err, execution_time=0.001),
        ExecutionResult(success=True, sql="good", result_data=[(1,)],
                        execution_time=0.002),
    ])
    fix_loop = SQLFixLoop(executor=executor, llm_client=MagicMock(), max_retries=2)
    fix_loop._try_fix = MagicMock(return_value="good")

    g = fix_loop.build_graph()
    result = g.invoke({
        "sql": "bad", "original_sql": "bad",
        "user_query": "q", "schema_text": "",
        "attempt": 0, "fix_history": [],
    })
    assert result["result"].success is True
    assert executor.execute.call_count == 2
    assert result["fix_history"] == ["good"]


def test_execution_subgraph_fail_exhaust_retries():
    """全部失败，达到 max_retries 后终止"""
    from src.execution.executor import (
        ErrorType,
        ExecutionResult,
        SQLExecutor,
        SQLFixLoop,
        StructuredError,
    )

    err = StructuredError(error_type=ErrorType.SYNTAX_ERROR, original_message="err")
    executor = MagicMock(spec=SQLExecutor)
    executor.execute = MagicMock(return_value=ExecutionResult(
        success=False, sql="bad", error=err, execution_time=0.001,
    ))
    fix_loop = SQLFixLoop(executor=executor, llm_client=MagicMock(), max_retries=1)
    fix_loop._try_fix = MagicMock(return_value="still_bad")

    g = fix_loop.build_graph()
    result = g.invoke({
        "sql": "bad", "original_sql": "bad",
        "user_query": "q", "schema_text": "",
        "attempt": 0, "fix_history": [],
    })
    assert result["result"].success is False
    # 第 1 次执行 + 第 1 次重试 = 2 次执行
    assert executor.execute.call_count == 2


# ============================================================================
# Decision 子图
# ============================================================================

def _mk_cand(sql, success=True, result=None, exec_time=0.01):
    from src.sql_generation.sql_generator import SQLCandidate, SQLStatus
    return SQLCandidate(
        id=sql[:4],
        sql=sql,
        status=SQLStatus.SUCCESS if success else SQLStatus.FAILED,
        result=result if result is not None else [(1,)],
        execution_time=exec_time,
    )


def test_decision_subgraph_no_candidates():
    from src.decision.self_consistency import SelfConsistencyDecision

    d = SelfConsistencyDecision()
    g = d.build_graph()
    result = g.invoke({"candidates": [], "user_query": "q"})
    assert result["final_decision"].selected_sql is None
    assert "无候选" in result["final_decision"].decision_reason


def test_decision_subgraph_all_failed():
    from src.decision.self_consistency import SelfConsistencyDecision

    d = SelfConsistencyDecision()
    c1 = _mk_cand("bad1", success=False)
    c2 = _mk_cand("bad2", success=False)
    g = d.build_graph()
    result = g.invoke({"candidates": [c1, c2], "user_query": "q"})
    assert "失败" in result["final_decision"].decision_reason


def test_decision_subgraph_majority():
    from src.decision.self_consistency import SelfConsistencyDecision

    d = SelfConsistencyDecision()
    # 3 个相同结果（多数），1 个不同
    c1 = _mk_cand("SELECT 1", result=[(1,)], exec_time=0.02)
    c2 = _mk_cand("SELECT 1 AS x", result=[(1,)], exec_time=0.01)  # 最快
    c3 = _mk_cand("SELECT 1 LIMIT 1", result=[(1,)], exec_time=0.03)
    c4 = _mk_cand("SELECT 2", result=[(2,)], exec_time=0.01)

    g = d.build_graph()
    result = g.invoke({"candidates": [c1, c2, c3, c4], "user_query": "q"})
    dec = result["final_decision"]
    assert dec.selected_sql == "SELECT 1 AS x"
    assert "多数一致" in dec.decision_reason


def test_decision_subgraph_llm_fallback():
    """各候选结果都不同，触发 LLM 决策"""
    from src.decision.self_consistency import SelfConsistencyDecision

    mock_llm = MagicMock()
    mock_llm.chat_json = MagicMock(return_value={"selected": 2, "reason": "best"})
    d = SelfConsistencyDecision(llm_client=mock_llm)

    c1 = _mk_cand("A", result=[(1,)])
    c2 = _mk_cand("B", result=[(2,)])
    c3 = _mk_cand("C", result=[(3,)])

    g = d.build_graph()
    result = g.invoke({"candidates": [c1, c2, c3], "user_query": "q"})
    dec = result["final_decision"]
    assert dec.selected_sql == "B"
    assert dec.voting_summary["llm_decided"] is True

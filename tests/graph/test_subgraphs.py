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
        KeywordGroup,
        RetrievedItem,
    )

    ir = InformationRetrieval()
    # 新契约：extract_keywords 返回 List[KeywordGroup]
    groups = [
        KeywordGroup(phrase="销售额", terms=["销售额", "sales"]),
        KeywordGroup(phrase="去年", terms=["去年", "last year"]),
    ]
    ir.extract_keywords = MagicMock(return_value=groups)

    # retrieve_values 接收扁平化的字符串列表
    ir.retrieve_values = MagicMock(return_value=[
        RetrievedItem(item_type="value", name="2023", table_name="t",
                      score=0.9, metadata={"column_name": "year"})
    ])

    # retrieve_schema 新契约：返回 Dict[phrase, List[RetrievedItem]]
    ir.retrieve_schema = MagicMock(return_value={
        "销售额": [RetrievedItem(item_type="column", name="amount",
                                  table_name="t", score=0.7,
                                  metadata={"database": ""})],
        "去年":   [RetrievedItem(item_type="column", name="year",
                                  table_name="t", score=0.6,
                                  metadata={"database": ""})],
    })

    g = ir.build_graph()
    result = g.invoke({"user_query": "去年销售额"})

    # keywords 现在是 KeywordGroup 列表
    assert len(result["keywords"]) == 2
    ctx = result["retrieved_context"]
    assert ctx is not None
    # ctx.keywords 是扁平化后的字符串列表（每组 phrase + terms 去重）
    assert len(ctx.keywords) >= 2
    assert ctx.lsh_hit_count == 1
    assert len(ctx.tables) >= 1
    assert len(ctx.columns) >= 1


def test_ir_subgraph_empty_lsh():
    """LSH 未配置时 retrieve_values 返回空，流程仍能走完"""
    from src.retrieval.information_retrieval import InformationRetrieval, KeywordGroup

    ir = InformationRetrieval()
    ir.extract_keywords = MagicMock(return_value=[KeywordGroup(phrase="x", terms=["x"])])
    ir.retrieve_values = MagicMock(return_value=[])
    ir.retrieve_schema = MagicMock(return_value={})

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
    import json
    mock_llm.stream = MagicMock(return_value=iter([(
        json.dumps({"candidates": [{"sql": "SELECT * FROM t", "reason": "test"}]}),
        None,
    )]))
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
    """决策 51：无候选时走全失败分支，路径 H（无候选可修）"""
    from src.decision.self_consistency import SelfConsistencyDecision

    d = SelfConsistencyDecision()
    g = d.build_graph()
    result = g.invoke({"candidates": [], "user_query": "q"})
    dec = result["final_decision"]
    # 路径 H：无候选导致 fix_failed
    assert dec.fix_failed is True
    assert dec.decision_path == "H"


def test_decision_subgraph_all_failed():
    """决策 51：全失败时走 H 路径（默认 mock 候选无 structured_error → 视为 UNKNOWN）"""
    from src.decision.self_consistency import SelfConsistencyDecision
    from src.execution.executor import ErrorType, StructuredError

    d = SelfConsistencyDecision()
    c1 = _mk_cand("bad1", success=False)
    c2 = _mk_cand("bad2", success=False)
    # 给一个不可修错误（默认走 H）
    c1.structured_error = StructuredError(ErrorType.TIMEOUT_ERROR, "timeout")
    c2.structured_error = StructuredError(ErrorType.PERMISSION_ERROR, "perm denied")
    g = d.build_graph()
    result = g.invoke({"candidates": [c1, c2], "user_query": "q"})
    dec = result["final_decision"]
    assert dec.fix_failed is True
    assert dec.decision_path == "H"  # 全是不可修类型


def test_decision_subgraph_majority():
    """决策 51：多数投票已废弃，改测 R1 评分（mock LLM 给最快候选最高分）"""
    from src.decision.self_consistency import SelfConsistencyDecision

    mock_llm = MagicMock()
    # R1 评分：c2 是 5（唯一最高），直接返回路径 A
    mock_llm.stream.return_value = iter([(__import__("json").dumps({
        "scores": [
            {"candidate_id": "SELE", "score": 4, "reason": ""},
            {"candidate_id": "SELE", "score": 5, "reason": ""},  # 注意 id 冲突
        ]
    }, ensure_ascii=False), None)])
    d = SelfConsistencyDecision(llm_client=mock_llm)

    # 用唯一 id 避免冲突
    c1 = _mk_cand("SELECT 1", result=[(1,)], exec_time=0.02)
    c1.id = "c1"
    c2 = _mk_cand("SELECT 1 AS x", result=[(1,)], exec_time=0.01)
    c2.id = "c2"

    # 修正 mock 评分使用真实 id
    mock_llm.stream.return_value = iter([(__import__("json").dumps({
        "scores": [
            {"candidate_id": "c1", "score": 3, "reason": ""},
            {"candidate_id": "c2", "score": 5, "reason": ""},
        ]
    }, ensure_ascii=False), None)])

    g = d.build_graph()
    result = g.invoke({"candidates": [c1, c2], "user_query": "q"})
    dec = result["final_decision"]
    assert dec.selected_candidate_id == "c2"
    assert dec.decision_path == "A"


def test_decision_subgraph_llm_fallback():
    """决策 51：原"多数 + LLM fallback"已废弃。改测 R1<5 时进入 SmartFix"""
    from src.decision.self_consistency import SelfConsistencyDecision
    from unittest.mock import MagicMock as MM

    mock_llm = MM()
    # R1 评分：所有候选都 < 5 → 进入 SmartFix
    mock_llm.stream.side_effect = [
    iter([(__import__("json").dumps(d, ensure_ascii=False), None)])
    for d in [
        # R1 评分
        {"scores": [
            {"candidate_id": "A", "score": 3, "reason": ""},
            {"candidate_id": "B", "score": 4, "reason": ""},
            {"candidate_id": "C", "score": 3, "reason": ""},
        ]},
        # SmartFix 调用（不会真正发生，因为没注入 fix_loop，节点会兜底失败）
    ]
]
    d = SelfConsistencyDecision(llm_client=mock_llm)

    c1 = _mk_cand("A", result=[(1,)])
    c1.id = "A"
    c2 = _mk_cand("B", result=[(2,)])
    c2.id = "B"
    c3 = _mk_cand("C", result=[(3,)])
    c3.id = "C"

    g = d.build_graph()
    result = g.invoke({"candidates": [c1, c2, c3], "user_query": "q"})
    dec = result["final_decision"]
    # 选中 B（R1 最高分=4）
    assert dec.selected_candidate_id == "B"
    # 未注入 fix_loop 且无 executor → 路径 E（SmartFix 兜底失败）
    assert dec.decision_path == "E"
    assert dec.fix_failed is True

# ============================================================================
# TaskDecomposer 主图集成测试（v2 精简版）
# ============================================================================
# 验证：
#   - EXECUTE 单意图：task_decomposer 放行 → ir → ... → END
#   - 前置拒答检测：pre_reject 拦截写操作 → END
#   - 多意图：task_decomposer multi → run_subqueries → aggregate_results
#   - checkpointer + thread_id 状态共享
#
# 运行: pytest tests/clarification/test_agent_integration.py -v
# ============================================================================

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from src.clarification.task_decomposer import TaskDecomposer


def _make_llm_mock(json_dict: dict):
    """mock LLM，stream 返回单 chunk JSON。"""
    mock = MagicMock()
    mock.stream.return_value = [(json.dumps(json_dict, ensure_ascii=False), None)]
    return mock


def _build_pipeline_mocks():
    """构造主图 ir/ss/cg/execution/decision 所需 Mock（最小成功路径）。"""
    from src.decision.self_consistency import DecisionResult
    from src.execution.executor import ExecutionResult
    from src.retrieval.information_retrieval import RetrievedContext, RetrievedItem
    from src.schema_selection.schema_selector import MSchemaColumn, MSchemaTable
    from src.sql_generation.sql_generator import SQLCandidate, SQLStatus

    retriever = MagicMock()
    ir_graph = MagicMock()
    ir_graph.invoke = MagicMock(return_value={
        "keywords": ["k1"],
        "retrieved_context": RetrievedContext(
            tables=[RetrievedItem(item_type="table", name="t", score=0.9)],
            columns=[RetrievedItem(item_type="column", name="c", table_name="t", score=0.8)],
            values=[], keywords=["k1"], lsh_hit_count=0, vector_top_scores=[0.8],
        ),
    })
    retriever.build_graph = MagicMock(return_value=ir_graph)

    selector = MagicMock()
    ss_graph = MagicMock()
    ss_graph.invoke = MagicMock(return_value={"selected_schema": [MSchemaTable(name="t", columns=[MSchemaColumn(name="c", data_type="INT")])]})
    selector.build_graph = MagicMock(return_value=ss_graph)

    generator = MagicMock()
    cg_graph = MagicMock()
    cg_graph.invoke = MagicMock(return_value={"sql_candidates": [SQLCandidate(id="c1", sql="SELECT * FROM t", status=SQLStatus.VALIDATED)]})
    generator.build_graph = MagicMock(return_value=cg_graph)

    fix_loop = MagicMock()
    fix_loop.executor.execute = MagicMock(return_value=ExecutionResult(
        success=True, sql="SELECT * FROM t", result_data=[(1,)], execution_time=0.001))

    decider = MagicMock()
    dec_graph = MagicMock()
    dec_graph.invoke = MagicMock(return_value={"final_decision": DecisionResult(
        selected_sql="SELECT * FROM t", selected_result=[(1,)],
        decision_path="A", selected_candidate_id="c1", decision_reason="majority",
        candidate_scores_r1=[], candidate_scores_r2=None, fix_failed=False,
        fix_rounds_used=0, last_error=None, voting_summary={})})
    decider.build_graph = MagicMock(return_value=dec_graph)

    return retriever, selector, generator, fix_loop, decider


def _build_graph(task_decomposer, with_checkpointer=True):
    from src.graph.main_graph import build_main_graph
    retriever, selector, generator, fix_loop, decider = _build_pipeline_mocks()
    return build_main_graph(
        retriever=retriever, selector=selector, generator=generator,
        fix_loop=fix_loop, decider=decider,
        task_decomposer=task_decomposer,
        checkpointer=InMemorySaver() if with_checkpointer else None,
    )


# ============================================================================
# 场景 1：EXECUTE 单意图放行
# ============================================================================
def test_execute_single_passes_through():
    llm = _make_llm_mock({"verdict": "execute", "intent_type": "single",
                          "subqueries": ["查苹果销售额"], "reason": "清晰"})
    planner = TaskDecomposer(llm_client=llm)
    graph = _build_graph(planner)

    config = {"configurable": {"thread_id": "t1"}}
    initial = {"user_query": "查苹果销售额", "query_id": "q1"}
    result = graph.invoke(initial, config)

    assert result["plan_result"]["verdict"] == "execute"
    assert result["subqueries"] == ["查苹果销售额"]
    # 走完整链路到 decision
    assert result.get("final_sql") == "SELECT * FROM t"


# ============================================================================
# 场景 2：前置拒答检测拦截写操作
# ============================================================================
def test_pre_reject_rejects_write_operation():
    """前置拒答检测硬性拦截写操作，不进 ir 和 task_decomposer"""
    llm = _make_llm_mock({"verdict": "execute", "intent_type": "single", "reason": "test"})
    planner = TaskDecomposer(llm_client=llm)
    retriever, selector, generator, fix_loop, decider = _build_pipeline_mocks()
    from src.graph.main_graph import build_main_graph
    graph = build_main_graph(
        retriever=retriever, selector=selector, generator=generator,
        fix_loop=fix_loop, decider=decider,
        task_decomposer=planner,
        checkpointer=InMemorySaver(),
    )

    config = {"configurable": {"thread_id": "t2"}}
    result = graph.invoke({"user_query": "删除数据", "query_id": "q2"}, config)

    # pre_reject 节点硬性写操作检测拦截
    assert result.get("rewrite_rejection_reason") is not None
    assert "写操作" in result.get("rejection_reason", "")
    # ir 不应被调用（pre_reject 短路）
    retriever.build_graph.assert_not_called()
    # 没有最终 SQL
    assert result.get("final_sql", "") == ""


# ============================================================================
# 场景 3：task_decomposer=None 向后兼容
# ============================================================================
def test_no_task_decomposer_backward_compatible():
    graph = _build_graph(task_decomposer=None)
    config = {"configurable": {"thread_id": "t5"}}
    result = graph.invoke({"user_query": "查苹果销售额", "query_id": "q5"}, config)
    assert result["subqueries"] == ["查苹果销售额"]
    assert result.get("final_sql") == "SELECT * FROM t"


# ============================================================================
# 场景 4：多意图 → run_subqueries → aggregate_results 完整路径
# ============================================================================
def test_multi_intent_runs_orchestrator():
    """task_decomposer multi → run_subqueries（orchestrator 串行）→ aggregate_results → memory_update"""
    from src.clarification.subquery_orchestrator import SubqueryOrchestrator
    from src.graph.main_graph import build_main_graph
    from src.graph.single_query_graph import build_single_query_graph

    llm = _make_llm_mock({
        "verdict": "execute", "intent_type": "multi",
        "subqueries": ["查苹果销售额", "查苹果利润"], "reason": "两个独立意图",
    })
    planner = TaskDecomposer(llm_client=llm)

    retriever, selector, generator, fix_loop, decider = _build_pipeline_mocks()
    sqg = build_single_query_graph(
        retriever=retriever, selector=selector, generator=generator,
        fix_loop=fix_loop, decider=decider,
    )
    orch = SubqueryOrchestrator(sqg)
    graph = build_main_graph(
        retriever=retriever, selector=selector, generator=generator,
        fix_loop=fix_loop, decider=decider,
        task_decomposer=planner,
        orchestrator=orch, summarizer=None,
        checkpointer=None, single_query_graph=sqg,
    )
    config = {"configurable": {"thread_id": "t7"}}
    result = graph.invoke({"user_query": "查苹果的销售额和利润", "query_id": "q7"}, config)

    assert result["plan_result"]["intent_type"] == "multi"
    assert len(result["subqueries"]) == 2
    # orchestrator 执行了 2 个子查询
    assert len(result["subquery_results"]) == 2
    # decision_path 标记为 MULTI
    assert result["decision_path"] == "MULTI"
    # summary_text 非空（降级拼接）
    assert result["summary_text"]
    # 走了 aggregate_results（summary_text 被填充）
    assert "子查询" in result["summary_text"]


def test_single_intent_does_not_trigger_orchestrator():
    """单意图走 ir 线性路径，不触发 run_subqueries"""
    from src.clarification.subquery_orchestrator import SubqueryOrchestrator
    from src.graph.main_graph import build_main_graph
    from src.graph.single_query_graph import build_single_query_graph

    llm = _make_llm_mock({"verdict": "execute", "intent_type": "single",
                          "subqueries": ["查苹果销售额"], "reason": "清晰"})
    planner = TaskDecomposer(llm_client=llm)
    retriever, selector, generator, fix_loop, decider = _build_pipeline_mocks()
    sqg = build_single_query_graph(
        retriever=retriever, selector=selector, generator=generator,
        fix_loop=fix_loop, decider=decider,
    )
    orch = SubqueryOrchestrator(sqg)
    graph = build_main_graph(
        retriever=retriever, selector=selector, generator=generator,
        fix_loop=fix_loop, decider=decider,
        task_decomposer=planner,
        orchestrator=orch, summarizer=None, checkpointer=None,
        single_query_graph=sqg,
    )
    config = {"configurable": {"thread_id": "t8"}}
    result = graph.invoke({"user_query": "查苹果销售额", "query_id": "q8"}, config)

    assert result["plan_result"]["intent_type"] == "single"
    # 单意图走 ir 线性路径，有 final_decision（非 None），subquery_results 为空
    assert result.get("final_decision") is not None or result.get("final_sql")
    assert result.get("subquery_results", []) == []


# ============================================================================
# 场景 5：UserMemory/SessionMemory 通过 ContextVar 注入，checkpointer 不报序列化错
# ============================================================================
def test_user_memory_via_contextvar_with_checkpointer():
    """回归：UserMemory/SessionMemory 是 Python 对象，checkpointer 不报错"""
    from src.api.streaming import current_user_memory, current_session_memory

    llm = _make_llm_mock({"verdict": "execute", "intent_type": "single",
                          "subqueries": ["查苹果销售额"], "reason": "清晰"})
    planner = TaskDecomposer(llm_client=llm)
    graph = _build_graph(planner, with_checkpointer=True)

    class _FakeUserMemory:
        def get_query_preferences(self):
            return {"limit": 10}

    class _FakeSessionMemory:
        session_id = "demo_session"

    um = _FakeUserMemory()
    sm = _FakeSessionMemory()

    config = {"configurable": {"thread_id": "t9"}}
    um_token = current_user_memory.set(um)
    sm_token = current_session_memory.set(sm)
    try:
        result = graph.invoke({"user_query": "查苹果销售额", "query_id": "q9"}, config)
        assert result["plan_result"]["verdict"] == "execute"
        assert result.get("final_sql") == "SELECT * FROM t"
    finally:
        current_user_memory.reset(um_token)
        current_session_memory.reset(sm_token)


# ============================================================================
# 场景 6：带 checkpointer 时 decision 子图的 fix_loop 不报序列化错（回归）
# ============================================================================
def test_decision_fix_loop_not_in_state_with_checkpointer():
    """回归：fix_loop 是 Python 对象，checkpointer 不应报错"""
    from src.api.streaming import current_fix_loop

    llm = _make_llm_mock({"verdict": "execute", "intent_type": "single",
                          "subqueries": ["查苹果销售额"], "reason": "清晰"})
    planner = TaskDecomposer(llm_client=llm)
    graph = _build_graph(planner, with_checkpointer=True)

    class _FakeFixLoop:
        def run(self, *a, **kw):
            return {"fixed_sql": "SELECT 1", "success": True}

    fl = _FakeFixLoop()

    config = {"configurable": {"thread_id": "t10"}}
    fl_token = current_fix_loop.set(fl)
    try:
        result = graph.invoke({"user_query": "查苹果销售额", "query_id": "q10"}, config)
        assert result["plan_result"]["verdict"] == "execute"
    finally:
        current_fix_loop.reset(fl_token)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
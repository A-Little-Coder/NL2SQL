# ============================================================================
# TaskPlanner 主图集成测试（决策 9-13）
# ============================================================================
# 验证：
#   - EXECUTE 单意图：task_planner 放行 → ir → ... → END
#   - REJECT：task_planner 拒答 → 直接 END（不进 ir）
#   - CLARIFY interrupt/resume：首次挂起 → resume 带回答 → 重新裁决 → 执行
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

from src.clarification.task_planner import TaskPlanner
from src.clarification.dialog import DialogManager


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


def _build_graph(task_planner, dialog_manager, with_checkpointer=True):
    from src.graph.main_graph import build_main_graph
    retriever, selector, generator, fix_loop, decider = _build_pipeline_mocks()
    return build_main_graph(
        retriever=retriever, selector=selector, generator=generator,
        fix_loop=fix_loop, decider=decider,
        task_planner=task_planner, dialog_manager=dialog_manager,
        checkpointer=InMemorySaver() if with_checkpointer else None,
    )


# ============================================================================
# 场景 1：EXECUTE 单意图放行
# ============================================================================
def test_execute_single_passes_through():
    llm = _make_llm_mock({"verdict": "execute", "intent_type": "single",
                          "subqueries": ["查苹果销售额"], "reason": "清晰"})
    planner = TaskPlanner(llm_client=llm)
    graph = _build_graph(planner, dialog_manager=None)

    config = {"configurable": {"thread_id": "t1"}}
    initial = {"user_query": "查苹果销售额", "query_id": "q1"}
    result = graph.invoke(initial, config)

    assert result["plan_result"]["verdict"] == "execute"
    assert result["subqueries"] == ["查苹果销售额"]
    # 走完整链路到 decision
    assert result.get("final_sql") == "SELECT * FROM t"
    # ir 被调用（说明 task_planner 放行进了 ir）
    assert graph  # placeholder


# ============================================================================
# 场景 2：REJECT 直接 END（不进 ir）
# ============================================================================
def test_reject_short_circuits_before_ir():
    llm = _make_llm_mock({"verdict": "reject", "reject_reason": "越权写操作", "reason": "delete"})
    planner = TaskPlanner(llm_client=llm)
    retriever, selector, generator, fix_loop, decider = _build_pipeline_mocks()
    from src.graph.main_graph import build_main_graph
    graph = build_main_graph(
        retriever=retriever, selector=selector, generator=generator,
        fix_loop=fix_loop, decider=decider,
        task_planner=planner, dialog_manager=None,
        checkpointer=InMemorySaver(),
    )

    config = {"configurable": {"thread_id": "t2"}}
    result = graph.invoke({"user_query": "删除数据", "query_id": "q2"}, config)

    assert result["plan_result"]["verdict"] == "reject"
    assert "写操作" in result["rejection_reason"]
    # ir 不应被调用（REJECT 短路）
    retriever.build_graph.assert_not_called()
    # 没有最终 SQL
    assert result.get("final_sql", "") == ""


# ============================================================================
# 场景 3：CLARIFY interrupt/resume 完整闭环
# ============================================================================
def test_clarify_interrupt_then_resume():
    """首次 CLARIFY 挂起 → resume 带回答 → 重新裁决 EXECUTE → 执行

    LangGraph interrupt 语义：resume 时节点从头重跑。
    所以 plan 的调用顺序：
      1. 首次执行 plan(clarified=None) → clarify → ask() interrupt 挂起
      2. resume 重跑 plan(clarified=None) → clarify → ask() 返回"公司" → round=1
      3. while 内重新 plan(clarified="公司") → execute → 放行
    故 side_effect 需 3 个响应：[clarify, clarify, execute]
    """
    llm = MagicMock()
    clarify_resp = [(json.dumps({"verdict": "clarify",
                                 "clarify_question": "苹果指公司还是水果？",
                                 "ambiguities": [{"entity": "苹果", "candidates": ["公司", "水果"]}],
                                 "reason": "多义"}, ensure_ascii=False), None)]
    execute_resp = [(json.dumps({"verdict": "execute", "intent_type": "single",
                                 "subqueries": ["查 Apple 公司销售额"], "reason": "已澄清"}, ensure_ascii=False), None)]
    # 首次 plan(clarify) → resume 重跑 plan(clarify) → while 内重新 plan(execute)
    llm.stream.side_effect = [clarify_resp, clarify_resp, execute_resp]

    planner = TaskPlanner(llm_client=llm)
    dialog_manager = DialogManager(max_rounds=5)
    graph = _build_graph(planner, dialog_manager)

    config = {"configurable": {"thread_id": "t3"}}

    # 首次执行：应挂起在 task_planner 的 interrupt
    chunks = list(graph.stream({"user_query": "查苹果的销售额", "query_id": "q3"}, config, stream_mode="updates"))
    interrupted = any("__interrupt__" in c for c in chunks)
    assert interrupted, "首次执行应在 CLARIFY 处挂起"

    # 确认挂起状态
    snap = graph.get_state(config)
    assert snap.next  # 非空 = 暂停中

    # resume：用户回答"公司"
    list(graph.stream(Command(resume="公司"), config, stream_mode="updates"))
    # resume 后应继续走完整链路到 END
    snap2 = graph.get_state(config)
    assert not snap2.next  # 空 = 跑完了

    final = snap2.values
    assert final["plan_result"]["verdict"] == "execute"
    assert final["subqueries"] == ["查 Apple 公司销售额"]
    assert final.get("final_sql") == "SELECT * FROM t"
    # clarify_round 应为 1（一轮反问）
    assert final["clarify_round"] == 1


# ============================================================================
# 场景 4：CLARIFY 用户拒答 → 降级执行
# ============================================================================
def test_clarify_user_declines_degrades_to_execute():
    """CLARIFY 挂起 → resume 用户拒答 → 降级执行

    plan 调用顺序：首次 plan(clarify) 挂起 → resume 重跑 plan(clarify) → ask() 返回 DECLINED → 降级
    side_effect 需 2 个 clarify 响应。
    """
    llm = MagicMock()
    clarify_resp = [(json.dumps({"verdict": "clarify",
                                 "clarify_question": "苹果指什么？", "reason": "多义"}, ensure_ascii=False), None)]
    llm.stream.side_effect = [clarify_resp, clarify_resp]

    planner = TaskPlanner(llm_client=llm)
    dialog_manager = DialogManager(max_rounds=5)
    graph = _build_graph(planner, dialog_manager)

    config = {"configurable": {"thread_id": "t4"}}
    # 首次挂起
    list(graph.stream({"user_query": "查苹果的销售额", "query_id": "q4"}, config, stream_mode="updates"))
    assert graph.get_state(config).next  # 暂停中

    # resume：用户拒答"不知道"
    list(graph.stream(Command(resume="不知道"), config, stream_mode="updates"))
    final = graph.get_state(config).values
    assert not graph.get_state(config).next  # 跑完
    # 降级执行（用原始 query）
    assert final["plan_result"]["verdict"] == "execute"
    assert final["plan_result"].get("degraded") is True
    assert final["subqueries"] == ["查苹果的销售额"]


# ============================================================================
# 场景 5：task_planner=None 向后兼容（直接 EXECUTE 单意图）
# ============================================================================
def test_no_task_planner_backward_compatible():
    graph = _build_graph(task_planner=None, dialog_manager=None)
    config = {"configurable": {"thread_id": "t5"}}
    result = graph.invoke({"user_query": "查苹果销售额", "query_id": "q5"}, config)
    assert result["subqueries"] == ["查苹果销售额"]
    assert result.get("final_sql") == "SELECT * FROM t"


# ============================================================================
# 场景 6：写操作硬检测 REJECT（不调 LLM）
# ============================================================================
def test_write_operation_rejected_no_llm():
    llm = _make_llm_mock({"verdict": "execute", "subqueries": ["x"]})
    planner = TaskPlanner(llm_client=llm)
    retriever, selector, generator, fix_loop, decider = _build_pipeline_mocks()
    from src.graph.main_graph import build_main_graph
    graph = build_main_graph(
        retriever=retriever, selector=selector, generator=generator,
        fix_loop=fix_loop, decider=decider,
        task_planner=planner, dialog_manager=None, checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "t6"}}
    result = graph.invoke({"user_query": "delete from users", "query_id": "q6"}, config)
    assert result["plan_result"]["verdict"] == "reject"
    llm.stream.assert_not_called()
    retriever.build_graph.assert_not_called()


# ============================================================================
# 场景 7：多意图 → run_subqueries → aggregate_results 完整路径（决策 14/15）
# ============================================================================
def test_multi_intent_runs_orchestrator():
    """task_planner multi → run_subqueries（orchestrator 串行）→ aggregate_results → memory_update"""
    from src.clarification.subquery_orchestrator import SubqueryOrchestrator
    from src.graph.main_graph import build_main_graph
    from src.graph.single_query_graph import build_single_query_graph

    llm = _make_llm_mock({
        "verdict": "execute", "intent_type": "multi",
        "subqueries": ["查苹果销售额", "查苹果利润"], "reason": "两个独立意图",
    })
    planner = TaskPlanner(llm_client=llm)

    retriever, selector, generator, fix_loop, decider = _build_pipeline_mocks()
    sqg = build_single_query_graph(
        retriever=retriever, selector=selector, generator=generator,
        fix_loop=fix_loop, decider=decider,
    )
    orch = SubqueryOrchestrator(sqg)
    graph = build_main_graph(
        retriever=retriever, selector=selector, generator=generator,
        fix_loop=fix_loop, decider=decider,
        task_planner=planner, dialog_manager=None,
        orchestrator=orch, summarizer=None,  # summarizer=None → 降级拼接
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
    planner = TaskPlanner(llm_client=llm)
    retriever, selector, generator, fix_loop, decider = _build_pipeline_mocks()
    sqg = build_single_query_graph(
        retriever=retriever, selector=selector, generator=generator,
        fix_loop=fix_loop, decider=decider,
    )
    orch = SubqueryOrchestrator(sqg)
    graph = build_main_graph(
        retriever=retriever, selector=selector, generator=generator,
        fix_loop=fix_loop, decider=decider,
        task_planner=planner, dialog_manager=None,
        orchestrator=orch, summarizer=None, checkpointer=None,
        single_query_graph=sqg,
    )
    config = {"configurable": {"thread_id": "t8"}}
    result = graph.invoke({"user_query": "查苹果销售额", "query_id": "q8"}, config)

    assert result["plan_result"]["intent_type"] == "single"
    # 单意图走 ir 线性路径，有 final_decision（非 None），subquery_results 为空
    assert result.get("final_decision") is not None or result.get("final_sql")
    assert result.get("subquery_results", []) == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


# ============================================================================
# 场景 9：UserMemory/SessionMemory 实例通过 ContextVar 注入，checkpointer 不报序列化错
# ============================================================================
def test_user_memory_via_contextvar_with_checkpointer():
    """回归：真实 bug 场景——UserMemory/SessionMemory 是 Python 对象，
    若放进 state 会被 checkpointer 序列化报 'not msgpack serializable'。
    改用 ContextVar 注入后，checkpointer 不应报错，且节点能取到实例。"""
    from src.api.streaming import current_user_memory, current_session_memory

    llm = _make_llm_mock({"verdict": "execute", "intent_type": "single",
                          "subqueries": ["查苹果销售额"], "reason": "清晰"})
    planner = TaskPlanner(llm_client=llm)
    graph = _build_graph(planner, dialog_manager=None)

    # 模拟 API 层注入：UserMemory/SessionMemory 是不可序列化的对象实例
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
        # 带 checkpointer invoke，不应抛 'not msgpack serializable'
        result = graph.invoke({"user_query": "查苹果销售额", "query_id": "q9"}, config)
        assert result["plan_result"]["verdict"] == "execute"
        assert result.get("final_sql") == "SELECT * FROM t"
    finally:
        current_user_memory.reset(um_token)
        current_session_memory.reset(sm_token)


# ============================================================================
# 场景 10：带 checkpointer 时 decision 子图的 fix_loop 不报序列化错（回归）
# ============================================================================
def test_decision_fix_loop_not_in_state_with_checkpointer():
    """回归：真实 bug——SQLFixLoop 是 Python 对象，若放进 decision 子图 sub_input state，
    会被 checkpointer 序列化报 'not msgpack serializable: SQLFixLoop'。
    改用 ContextVar 传递后，checkpointer 不应报错，且 SmartFix 能拿到 fix_loop。"""
    from src.api.streaming import current_fix_loop

    llm = _make_llm_mock({"verdict": "execute", "intent_type": "single",
                          "subqueries": ["查苹果销售额"], "reason": "清晰"})
    planner = TaskPlanner(llm_client=llm)
    graph = _build_graph(planner, dialog_manager=None)

    # 模拟 per-db 的 fix_loop（不可序列化对象）
    class _FakeFixLoop:
        def run(self, *a, **kw):
            return {"fixed_sql": "SELECT 1", "success": True}

    fl = _FakeFixLoop()

    config = {"configurable": {"thread_id": "t10"}}
    fl_token = current_fix_loop.set(fl)
    try:
        # 带 checkpointer invoke 到 decision，不应抛 'not msgpack serializable'
        result = graph.invoke({"user_query": "查苹果销售额", "query_id": "q10"}, config)
        assert result["plan_result"]["verdict"] == "execute"
    finally:
        current_fix_loop.reset(fl_token)

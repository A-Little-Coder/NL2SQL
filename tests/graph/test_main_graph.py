"""
§18.10 主图端到端测试（Mock 所有 Agent）

验证：
- 主图能从 START 跑到 END
- 状态在节点间正确流动（IR → SS → AnswerabilityCheck → CG → Execution → Decision）
- 条件边：SS 无 schema 时短路、AnswerabilityCheck 拒答时短路、CG 无候选时短路
- Decision 结果验证不可信时写入 rejection_reason
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
    # 决策 51：ExecuteAll 直接调用 fix_loop.executor.execute()，不再走 fix_loop.build_graph()
    fix_loop = MagicMock()
    fix_loop.executor.execute = MagicMock(return_value=ExecutionResult(
        success=True, sql="SELECT * FROM t",
        result_data=[(1,)], execution_time=0.001,
    ))
    # 保留 build_graph mock 用于向后兼容（虽然新代码不会用到）
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
    decider.result_verifier = None  # 默认无结果验证器

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
    # trace_log 记录了各节点（决策 51：Execution 节点 trace 改为 ExecuteAll）
    log = " ".join(result.get("trace_log", []))
    assert "IR" in log and "SS" in log and "CG" in log
    assert "ExecuteAll" in log and "Decision" in log


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
    result = graph.invoke(state)

    # v2 TaskDecomposer 不再设 clarification_done，但 final_sql 应正常输出
    assert result.get("final_sql")


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


# ============================================================================
# 可回答性检查（决策 23）测试
# ============================================================================

def test_main_graph_with_answerability_check_pass():
    """AnswerabilityCheck 放行时正常走完流程"""
    from src.graph import build_main_graph, create_initial_state
    from src.verification.answerability import AnswerabilityResult

    retr, sel, gen, fix, dec = _build_complete_mocks(success_path=True)
    checker = MagicMock()
    checker.check = MagicMock(return_value=AnswerabilityResult(
        answerable="true", confidence=0.9, reason="OK",
    ))

    graph = build_main_graph(retr, sel, gen, fix, dec,
                             answerability_checker=checker)
    state = create_initial_state(user_query="查询")
    result = graph.invoke(state)

    assert result["final_sql"] == "SELECT * FROM t"
    assert result["answerability_result"]["answerable"] == "true"
    log = " ".join(result.get("trace_log", []))
    assert "AnswerabilityCheck" in log


def test_main_graph_with_answerability_check_reject():
    """AnswerabilityCheck 拒答时主图直接 END，不进 CG"""
    from src.graph import build_main_graph, create_initial_state
    from src.verification.answerability import AnswerabilityResult

    retr, sel, gen, fix, dec = _build_complete_mocks(success_path=True)
    checker = MagicMock()
    checker.check = MagicMock(return_value=AnswerabilityResult(
        answerable="false", confidence=0.9,
        reason="数据粒度不匹配：问学生但只有学校级别数据",
        missing_info="学生粒度数据",
        granularity_match="mismatch",
    ))

    graph = build_main_graph(retr, sel, gen, fix, dec,
                             answerability_checker=checker)
    state = create_initial_state(user_query="每个学生的成绩")
    result = graph.invoke(state)

    # 不应进入 CG / Execution / Decision
    gen.build_graph.assert_not_called()
    fix.build_graph.assert_not_called()
    dec.build_graph.assert_not_called()
    # 拒答原因
    assert result.get("rejection_reason") is not None
    assert result["answerability_result"]["answerable"] == "false"


def test_main_graph_answerability_uncertain_passes():
    """AnswerabilityCheck 返回 uncertain 时放行"""
    from src.graph import build_main_graph, create_initial_state
    from src.verification.answerability import AnswerabilityResult

    retr, sel, gen, fix, dec = _build_complete_mocks(success_path=True)
    checker = MagicMock()
    checker.check = MagicMock(return_value=AnswerabilityResult(
        answerable="uncertain", confidence=0.5, reason="不确定",
    ))

    graph = build_main_graph(retr, sel, gen, fix, dec,
                             answerability_checker=checker)
    state = create_initial_state(user_query="可能能查")
    result = graph.invoke(state)

    # 应走完整个流程
    assert result["final_sql"] == "SELECT * FROM t"


# ============================================================================
# 结果可信度验证（决策 24）测试
# ============================================================================

def test_main_graph_decision_result_verification_reject():
    """Decision 结果验证不可信时写入 rejection_reason，清空最终结果"""
    from src.graph import build_main_graph, create_initial_state
    from src.decision.self_consistency import DecisionResult

    retr, sel, gen, fix, dec = _build_complete_mocks(success_path=True)
    # 决策 51：verify 已移入 Decision 子图末尾。
    # mock 子图直接返回包含 verification 的 DecisionResult
    dec_graph = MagicMock()
    dec_graph.invoke = MagicMock(return_value={
        "final_decision": DecisionResult(
            selected_sql="SELECT * FROM t",
            selected_result=[(1,)],
            execution_time=0.001,
            decision_reason="mock",
            voting_summary={
                "verification": {
                    "trustworthy": "false",
                    "reason": "SQL 查学校但问学生，粒度不匹配",
                    "should_reject": True,
                },
            },
        ),
    })
    dec.build_graph = MagicMock(return_value=dec_graph)

    graph = build_main_graph(retr, sel, gen, fix, dec)
    state = create_initial_state(user_query="每个学生的成绩")
    result = graph.invoke(state)

    # 结果被拒绝
    assert result.get("rejection_reason") is not None
    assert "不可信" in result["rejection_reason"]
    assert result["final_sql"] == ""
    assert result["final_result"] is None
    assert result["result_verification"]["trustworthy"] == "false"


def test_main_graph_decision_result_verification_pass():
    """Decision 结果验证可信时正常返回"""
    from src.graph import build_main_graph, create_initial_state
    from src.decision.self_consistency import DecisionResult

    retr, sel, gen, fix, dec = _build_complete_mocks(success_path=True)
    # 决策 51：mock 子图返回 verification 通过的 DecisionResult
    dec_graph = MagicMock()
    dec_graph.invoke = MagicMock(return_value={
        "final_decision": DecisionResult(
            selected_sql="SELECT * FROM t",
            selected_result=[(1,)],
            execution_time=0.001,
            decision_reason="mock",
            voting_summary={
                "verification": {
                    "trustworthy": "true",
                    "reason": "SQL 与问题对齐",
                    "should_reject": False,
                },
            },
        ),
    })
    dec.build_graph = MagicMock(return_value=dec_graph)

    graph = build_main_graph(retr, sel, gen, fix, dec)
    state = create_initial_state(user_query="各校平均分")
    result = graph.invoke(state)

    assert result["final_sql"] == "SELECT * FROM t"
    assert result["result_verification"]["trustworthy"] == "true"
    assert result.get("rejection_reason") is None


# ============================================================================
# harden-history-cache：主图命中路径端到端（history_cache -> value_rewrite ->
# cache_confirm -> run_single_query），用预置 cache_confirm_approved 跳过 interrupt
# ============================================================================

def _make_hit_history_cache(cached_sql="SELECT SUM(amt) FROM orders WHERE region='华东'",
                            historical_query="华东区的销售额"):
    """构造一个命中的 history_cache mock（check 返回 hit=True）"""
    from src.memory.history_cache import CacheResult

    hc = MagicMock()
    hc.recall_session_history = MagicMock(return_value=[])
    hc.check = MagicMock(return_value=CacheResult(
        hit=True,
        cached_sql=cached_sql,
        source="session_history",
        confidence=0.9,
        historical_query=historical_query,
    ))
    return hc


def _make_value_rewrite_llm(adjusted_sql="SELECT SUM(amt) FROM orders WHERE region='华北'"):
    """构造 value_rewrite 用的 llm_client mock"""
    llm = MagicMock()
    llm.invoke = MagicMock(return_value={
        "adjusted_sql": adjusted_sql,
        "changed": True,
        "reason": "region '华东' -> '华北'",
    })
    return llm


def test_main_graph_cache_hit_confirm_approve():
    """cache 命中 + 用户认同 -> 经 value_rewrite/cache_confirm -> execution 用 adjusted_cached_sql，跳过 ir/ss/cg"""
    from src.graph import build_main_graph, create_initial_state

    retr, sel, gen, fix, dec = _build_complete_mocks(success_path=True)
    hc = _make_hit_history_cache()
    llm = _make_value_rewrite_llm()

    graph = build_main_graph(retr, sel, gen, fix, dec, history_cache=hc, llm_client=llm)

    state = create_initial_state(user_query="华北区的销售额")
    state["cache_confirm_approved"] = True  # 测试逃逸：跳过 interrupt
    result = graph.invoke(state)

    # 命中标记与值改写产物落位
    assert result["cache_hit"] is True
    assert result["adjusted_cached_sql"] == "SELECT SUM(amt) FROM orders WHERE region='华北'"
    assert result["cached_historical_query"] == "华东区的销售额"
    assert result["cache_confirm_approved"] is True
    # value_rewrite LLM 被调用
    llm.invoke.assert_called_once()
    # cache 命中短路：ir/ss/cg 未触发
    gen.build_graph.assert_not_called()
    # execution 用 adjusted_cached_sql 执行
    fix.executor.execute.assert_called_with("SELECT SUM(amt) FROM orders WHERE region='华北'")
    assert result["final_sql"] == "SELECT * FROM t"


def test_main_graph_cache_hit_confirm_reject_fallback():
    """cache 命中 + 用户否定 -> cache_confirm 置 cache_hit=False -> 回退完整 ir 链路，final_sql 来自新生成"""
    from src.graph import build_main_graph, create_initial_state

    retr, sel, gen, fix, dec = _build_complete_mocks(success_path=True)
    hc = _make_hit_history_cache(cached_sql="SELECT DISTINCT cached_only")
    llm = _make_value_rewrite_llm(adjusted_sql="SELECT DISTINCT cached_only_adj")

    graph = build_main_graph(retr, sel, gen, fix, dec, history_cache=hc, llm_client=llm)

    state = create_initial_state(user_query="华北区的销售额")
    state["cache_confirm_approved"] = False  # 测试逃逸：否定
    result = graph.invoke(state)

    # 否定 -> cache_confirm 置 cache_hit=False、清空 cached_sql
    assert result["cache_confirm_approved"] is False
    assert result["cache_hit"] is False
    # 回退完整 ir 链路：cg 被触发
    gen.build_graph.assert_called()
    # final_sql 来自新生成（mock generator -> "SELECT * FROM t"），而非 cached_sql
    assert result["final_sql"] == "SELECT * FROM t"
    assert "cached_only" not in result["final_sql"]


def test_main_graph_cache_miss_skips_value_rewrite_confirm():
    """cache 未命中 -> 不进 value_rewrite/cache_confirm，直接 task_decomposer -> run_single_query"""
    from src.graph import build_main_graph, create_initial_state
    from src.memory.history_cache import CacheResult

    retr, sel, gen, fix, dec = _build_complete_mocks(success_path=True)
    hc = MagicMock()
    hc.recall_session_history = MagicMock(return_value=[])
    hc.check = MagicMock(return_value=CacheResult(hit=False))  # 未命中
    llm = _make_value_rewrite_llm()

    graph = build_main_graph(retr, sel, gen, fix, dec, history_cache=hc, llm_client=llm)

    state = create_initial_state(user_query="一个全新问题")
    result = graph.invoke(state)

    # 未命中：value_rewrite LLM 不应被调用
    llm.invoke.assert_not_called()
    # 走正常 ir/ss/cg 链路
    gen.build_graph.assert_called()
    assert result["final_sql"] == "SELECT * FROM t"
    assert result.get("cache_hit") is False

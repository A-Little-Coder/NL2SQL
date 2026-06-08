"""
NL2SQL 主图：串联 IR → (Clarification) → SS → AnswerabilityCheck → CG → Execution → Decision

依据 决策 22 / §18.2，决策 23 / §15.4-15.5，决策 24 / §15.6。

设计要点：
1. 节点是「适配器」，把主图 NL2SQLState 的字段映射到 Agent 子图的内部 State，
   并把子图输出再映射回主图 State。
2. 各 Agent 通过工厂 build_main_graph(config) 注入：调用方在 config 中传入
   已构造好的 InformationRetrieval / SchemaSelector / SQLGenerator /
   SQLFixLoop / SelfConsistencyDecision 实例。这样保证：
     - 现有公开 API 不变
     - 测试中可注入 Mock Agent
3. 条件边覆盖兜底：
     - IR 后无任何候选 → 主图直接 END（带 error）
     - SS 后无表 → END
     - AnswerabilityCheck 后不可回答 → END（拒答 + 原因）
     - CG 后无候选 SQL → END
     - Execution 后无成功结果时仍进入 Decision（Decision 会输出"全部失败"）
     - Decision 后结果不可信 → END（拒答 + 原因）

Clarification 节点本期占位（pass-through），Phase 2 接入。
"""

from typing import Any, Callable, Dict, Optional

from langgraph.graph import END, START, StateGraph

from src.graph.state import NL2SQLState


# ---------------------------------------------------------------------------
# 节点工厂：每个节点把主图 state 转为 Agent 子图所需的局部 state
# ---------------------------------------------------------------------------

def make_history_cache_node(history_cache) -> Callable[[NL2SQLState], Dict[str, Any]]:
    """构造 HistoryCache 节点：检查历史命中，命中时设置 cache_hit=True 并注入 cached_sql"""

    def node(state: NL2SQLState) -> Dict[str, Any]:
        if history_cache is None:
            return {
                "cache_hit": False,
                "trace_log": state.get("trace_log", []) + ["[HistoryCache] disabled"],
            }

        session_memory = state.get("_session_memory")
        user_memory = state.get("_user_memory")
        conversation_history = state.get("conversation_history", [])
        metric_definitions = state.get("metric_definitions", [])

        result = history_cache.check(
            user_query=state["user_query"],
            session_history=conversation_history,
            metric_definitions=metric_definitions,
        )

        out: Dict[str, Any] = {
            "cache_hit": result.hit,
            "cached_sql": result.cached_sql,
            "cache_source": result.source,
            "cache_confidence": result.confidence,
            "trace_log": state.get("trace_log", [])
                         + [f"[HistoryCache] hit={result.hit}, source={result.source}, confidence={result.confidence}"],
        }
        return out

    return node


def make_memory_update_node(updater) -> Callable[[NL2SQLState], Dict[str, Any]]:
    """构造 MemoryUpdate 节点：自动学习用户记忆和会话记忆"""

    def node(state: NL2SQLState) -> Dict[str, Any]:
        if updater is None:
            return {
                "trace_log": state.get("trace_log", []) + ["[MemoryUpdate] disabled"],
            }

        session_memory = state.get("_session_memory")
        user_memory = state.get("_user_memory")

        if user_memory is not None and session_memory is not None:
            updater.update(user_memory, session_memory, state)

        return {
            "trace_log": state.get("trace_log", []) + ["[MemoryUpdate] done"],
        }

    return node


def make_ir_node(retriever) -> Callable[[NL2SQLState], Dict[str, Any]]:
    """构造 IR 节点：调用 IR 子图，输出 keywords/retrieved_context"""

    def node(state: NL2SQLState) -> Dict[str, Any]:
        sub = retriever.build_graph()
        result = sub.invoke({
            "user_query": state["user_query"],
            "database_filter": state.get("database_filter"),
            "conversation_history": state.get("conversation_history", []),
        })
        return {
            "keywords": result.get("keywords", []),
            "retrieved_context": result.get("retrieved_context"),
            "trace_log": state.get("trace_log", []) + ["[IR] done"],
        }

    return node


def make_clarification_node() -> Callable[[NL2SQLState], Dict[str, Any]]:
    """
    Clarification 节点占位（Phase 2 接入）

    本期默认设置 clarification_done=True 让流程继续；
    Phase 2 时替换为 ClarificationAgent.build_graph() 调用。
    """

    def node(state: NL2SQLState) -> Dict[str, Any]:
        return {
            "clarification_done": True,
            "trace_log": state.get("trace_log", []) + ["[Clarification] skipped"],
        }

    return node


def make_ss_node(selector) -> Callable[[NL2SQLState], Dict[str, Any]]:
    """构造 SS 节点：调用 SS 子图，输出 selected_schema"""

    def node(state: NL2SQLState) -> Dict[str, Any]:
        ctx = state.get("retrieved_context")
        if ctx is None:
            return {"error": "IR 未产出 retrieved_context",
                    "selected_schema": []}
        sub = selector.build_graph()
        result = sub.invoke({
            "user_query": state["user_query"],
            "retrieved_context": ctx,
        })
        return {
            "selected_schema": result.get("selected_schema", []),
            "trace_log": state.get("trace_log", []) + ["[SS] done"],
        }

    return node


def make_answerability_check_node(checker) -> Callable[[NL2SQLState], Dict[str, Any]]:
    """
    构造可回答性检查节点（决策 23）：SS 之后、CG 之前

    宽松原则：只有 answerable="false" 才拦截，uncertain 放行。
    """

    def node(state: NL2SQLState) -> Dict[str, Any]:
        schema = state.get("selected_schema", [])
        ir_ctx = state.get("retrieved_context")
        result = checker.check(
            user_query=state["user_query"],
            mschema=schema,
            ir_context=ir_ctx,
        )
        out: Dict[str, Any] = {
            "answerability_result": result.to_dict(),
            "trace_log": state.get("trace_log", [])
                         + [f"[AnswerabilityCheck] {result.answerable}"],
        }
        if result.should_reject:
            out["rejection_reason"] = result.reason
            out["error"] = f"不可回答: {result.reason}"
        return out

    return node


def make_cg_node(generator) -> Callable[[NL2SQLState], Dict[str, Any]]:
    """构造 CG 节点：调用 CG 子图，输出 sql_candidates"""

    def node(state: NL2SQLState) -> Dict[str, Any]:
        schema = state.get("selected_schema", [])
        if not schema:
            return {"error": "SS 未产出 selected_schema",
                    "sql_candidates": []}
        sub = generator.build_graph()
        result = sub.invoke({
            "user_query": state["user_query"],
            "selected_schema": schema,
            "query_preferences": state.get("_user_memory").get_query_preferences()
                                  if state.get("_user_memory") else {},
            "metric_definitions": state.get("metric_definitions", []),
        })
        return {
            "sql_candidates": result.get("sql_candidates", []),
            "trace_log": state.get("trace_log", []) + ["[CG] done"],
        }

    return node


def make_execution_node(fix_loop) -> Callable[[NL2SQLState], Dict[str, Any]]:
    """
    构造 Execution 节点：对每个候选 SQL 调用 Execution 子图，回填结果到 candidate

    注意 Execution 子图操作的是单条 SQL，这里在主图层面做 for-loop。
    （也可以用 LangGraph Send API 做并行，本期保持简单）。

    当 cache_hit=True 时，从 cached_sql 构造候选并直接执行（跳过 IR/SS/CG）。
    """
    from src.sql_generation.sql_generator import SQLCandidate, SQLStatus
    from src.schema_selection.schema_selector import MSchemaFormat

    def node(state: NL2SQLState) -> Dict[str, Any]:
        # 如果 history_cache 命中，从 cached_sql 构造候选
        if state.get("cache_hit", False):
            cached_sql = state.get("cached_sql", "")
            if not cached_sql:
                return {"error": "cache_hit=True 但 cached_sql 为空"}

            cand = SQLCandidate(
                id="cache_hit",
                sql=cached_sql,
                status=SQLStatus.PENDING,
            )
            candidates = [cand]
        else:
            candidates = state.get("sql_candidates", [])

        if not candidates:
            return {"error": "CG 未产出 sql_candidates"}

        # 准备 schema_text 供 LLM 修复使用（只有有 schema 时才生成）
        schema = state.get("selected_schema", [])
        try:
            if schema:
                mschema_dict = MSchemaFormat.create_mschema_schema(schema)
                schema_text = MSchemaFormat.format_for_llm(mschema_dict)
            else:
                schema_text = ""
        except Exception:
            schema_text = ""

        sub = fix_loop.build_graph()
        for cand in candidates:
            try:
                result = sub.invoke({
                    "sql": cand.sql,
                    "original_sql": cand.sql,
                    "user_query": state["user_query"],
                    "schema_text": schema_text,
                    "attempt": 0,
                    "fix_history": [],
                })
                exec_result = result.get("result")
                if exec_result is None:
                    continue
                cand.result = exec_result.result_data
                cand.execution_time = exec_result.execution_time
                cand.status = (
                    SQLStatus.SUCCESS if exec_result.success else SQLStatus.FAILED
                )
                cand.error_message = (
                    exec_result.error.original_message if exec_result.error else None
                )
            except Exception as e:
                cand.status = SQLStatus.FAILED
                cand.error_message = str(e)

        return {
            "sql_candidates": candidates,
            "schema_text": schema_text,
            "trace_log": state.get("trace_log", []) + ["[Execution] done"],
        }

    return node


def make_decision_node(decider) -> Callable[[NL2SQLState], Dict[str, Any]]:
    """
    构造 Decision 节点：调用 Decision 子图，输出 final_decision

    决策 24：选定 SQL 后进行结果可信度验证，不可信时写入 rejection_reason。
    """

    def node(state: NL2SQLState) -> Dict[str, Any]:
        cands = state.get("sql_candidates", [])
        sub = decider.build_graph()
        result = sub.invoke({
            "candidates": cands,
            "user_query": state["user_query"],
        })
        decision = result.get("final_decision")
        out: Dict[str, Any] = {
            "final_decision": decision,
            "trace_log": state.get("trace_log", []) + ["[Decision] done"],
        }
        if decision is not None:
            out["final_sql"] = decision.selected_sql or ""
            out["final_result"] = decision.selected_result

            # 结果可信度验证（决策 24）
            if decider.result_verifier is not None and decision.selected_sql:
                mschema = state.get("selected_schema", [])
                verification = decider.result_verifier.verify(
                    user_query=state["user_query"],
                    selected_sql=decision.selected_sql,
                    result_sample=decision.selected_result,
                    mschema=mschema,
                )
                out["result_verification"] = verification.to_dict()
                if verification.should_reject:
                    out["rejection_reason"] = f"结果不可信: {verification.reason}"
                    out["final_sql"] = ""
                    out["final_result"] = None

        return out

    return node


# ---------------------------------------------------------------------------
# 主图构建
# ---------------------------------------------------------------------------

def build_main_graph(
    retriever,
    selector,
    generator,
    fix_loop,
    decider,
    *,
    answerability_checker=None,
    enable_clarification: bool = False,
    history_cache=None,
    memory_updater=None,
):
    """
    构造并编译 NL2SQL 主图

    Args:
        retriever: InformationRetrieval 实例（须实现 build_graph()）
        selector: SchemaSelector 实例
        generator: SQLGenerator 实例
        fix_loop: SQLFixLoop 实例
        decider: SelfConsistencyDecision 实例
        answerability_checker: AnswerabilityChecker 实例（决策 23，可选）
        enable_clarification: 是否启用 Clarification 节点（Phase 2）
        history_cache: HistoryCache 实例（决策 30，可选；None 时跳过）
        memory_updater: MemoryUpdater 实例（决策 29，可选；None 时跳过）

    Returns:
        CompiledGraph: 已编译主图，可调用 .invoke(initial_state)
    """
    graph = StateGraph(NL2SQLState)

    # 新增节点：历史命中检测（START 之后，IR 之前）
    graph.add_node("history_cache", make_history_cache_node(history_cache))
    graph.add_node("ir", make_ir_node(retriever))
    graph.add_node("clarification", make_clarification_node())
    graph.add_node("ss", make_ss_node(selector))

    # 可回答性检查节点（决策 23）：SS 之后、CG 之前
    if answerability_checker is not None:
        graph.add_node(
            "answerability_check",
            make_answerability_check_node(answerability_checker),
        )

    graph.add_node("cg", make_cg_node(generator))
    graph.add_node("execution", make_execution_node(fix_loop))
    graph.add_node("decision", make_decision_node(decider))

    # 新增节点：记忆自动学习（decision 之后，END 之前）
    graph.add_node("memory_update", make_memory_update_node(memory_updater))

    # 入口 → history_cache
    graph.add_edge(START, "history_cache")

    # HistoryCache 条件分支：命中 → execution（复用缓存 SQL）；否则 → ir
    def route_after_cache(state: NL2SQLState) -> str:
        if state.get("cache_hit", False):
            return "execution"
        return "ir"

    graph.add_conditional_edges(
        "history_cache",
        route_after_cache,
        {"ir": "ir", "execution": "execution"},
    )
    graph.add_edge("ir", "clarification")

    # Clarification 条件分支：done → SS，否则循环回自身（Phase 2 实装）
    def route_after_clarification(state: NL2SQLState) -> str:
        return "ss" if state.get("clarification_done", True) else "clarification"

    graph.add_conditional_edges(
        "clarification",
        route_after_clarification,
        {"ss": "ss", "clarification": "clarification"},
    )

    # SS 后：无 schema → END；有 schema → answerability_check 或 CG
    if answerability_checker is not None:
        def route_after_ss(state: NL2SQLState) -> str:
            if not state.get("selected_schema"):
                return END
            return "answerability_check"

        graph.add_conditional_edges(
            "ss", route_after_ss,
            {"answerability_check": "answerability_check", END: END},
        )

        # 可回答性检查条件分支（决策 23）：false → END（拒答），否则 → CG
        def route_after_answerability(state: NL2SQLState) -> str:
            check = state.get("answerability_result")
            if check and check.get("answerable") == "false":
                return END
            return "cg"

        graph.add_conditional_edges(
            "answerability_check",
            route_after_answerability,
            {"cg": "cg", END: END},
        )
    else:
        def route_after_ss(state: NL2SQLState) -> str:
            return "cg" if state.get("selected_schema") else END

        graph.add_conditional_edges(
            "ss", route_after_ss, {"cg": "cg", END: END},
        )

    # CG 后：无候选直接结束
    def route_after_cg(state: NL2SQLState) -> str:
        return "execution" if state.get("sql_candidates") else END

    graph.add_conditional_edges(
        "cg", route_after_cg, {"execution": "execution", END: END}
    )

    graph.add_edge("execution", "decision")
    graph.add_edge("decision", "memory_update")
    graph.add_edge("memory_update", END)

    return graph.compile()

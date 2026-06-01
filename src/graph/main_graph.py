"""
NL2SQL 主图：串联 IR → (Clarification) → SS → CG → Execution → Decision

依据 决策 22 / §18.2。

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
     - CG 后无候选 SQL → END
     - Execution 后无成功结果时仍进入 Decision（Decision 会输出"全部失败"）

Clarification 节点本期占位（pass-through），Phase 2 接入。
"""

from typing import Any, Callable, Dict, Optional

from langgraph.graph import END, START, StateGraph

from src.graph.state import NL2SQLState


# ---------------------------------------------------------------------------
# 节点工厂：每个节点把主图 state 转为 Agent 子图所需的局部 state
# ---------------------------------------------------------------------------

def make_ir_node(retriever) -> Callable[[NL2SQLState], Dict[str, Any]]:
    """构造 IR 节点：调用 IR 子图，输出 keywords/retrieved_context"""

    def node(state: NL2SQLState) -> Dict[str, Any]:
        sub = retriever.build_graph()
        result = sub.invoke({
            "user_query": state["user_query"],
            "database_filter": state.get("database_filter"),
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
    """
    from src.sql_generation.sql_generator import SQLStatus
    from src.schema_selection.schema_selector import MSchemaFormat

    def node(state: NL2SQLState) -> Dict[str, Any]:
        candidates = state.get("sql_candidates", [])
        if not candidates:
            return {"error": "CG 未产出 sql_candidates"}

        # 准备 schema_text 供 LLM 修复使用
        schema = state.get("selected_schema", [])
        try:
            mschema_dict = MSchemaFormat.create_mschema_schema(schema)
            schema_text = MSchemaFormat.format_for_llm(mschema_dict)
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
    """构造 Decision 节点：调用 Decision 子图，输出 final_decision"""

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
    enable_clarification: bool = False,
):
    """
    构造并编译 NL2SQL 主图

    Args:
        retriever: InformationRetrieval 实例（须实现 build_graph()）
        selector: SchemaSelector 实例
        generator: SQLGenerator 实例
        fix_loop: SQLFixLoop 实例
        decider: SelfConsistencyDecision 实例
        enable_clarification: 是否启用 Clarification 节点（Phase 2）

    Returns:
        CompiledGraph: 已编译主图，可调用 .invoke(initial_state)
    """
    graph = StateGraph(NL2SQLState)

    graph.add_node("ir", make_ir_node(retriever))
    graph.add_node("clarification", make_clarification_node())
    graph.add_node("ss", make_ss_node(selector))
    graph.add_node("cg", make_cg_node(generator))
    graph.add_node("execution", make_execution_node(fix_loop))
    graph.add_node("decision", make_decision_node(decider))

    # 主线
    graph.add_edge(START, "ir")
    graph.add_edge("ir", "clarification")

    # Clarification 条件分支：done → SS，否则循环回自身（Phase 2 实装）
    def route_after_clarification(state: NL2SQLState) -> str:
        return "ss" if state.get("clarification_done", True) else "clarification"

    graph.add_conditional_edges(
        "clarification",
        route_after_clarification,
        {"ss": "ss", "clarification": "clarification"},
    )

    # SS 后：无 schema 直接结束
    def route_after_ss(state: NL2SQLState) -> str:
        return "cg" if state.get("selected_schema") else END

    graph.add_conditional_edges(
        "ss", route_after_ss, {"cg": "cg", END: END}
    )

    # CG 后：无候选直接结束
    def route_after_cg(state: NL2SQLState) -> str:
        return "execution" if state.get("sql_candidates") else END

    graph.add_conditional_edges(
        "cg", route_after_cg, {"execution": "execution", END: END}
    )

    graph.add_edge("execution", "decision")
    graph.add_edge("decision", END)

    return graph.compile()

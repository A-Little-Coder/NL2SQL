"""单查询流水线编译图（refactor-single-query-graph）

抽取自主图 ir → ss → answerability_check → cg → execution → decision 节点链，
作为单意图路径、history_cache 命中路径、多意图串行编排三处的**单一事实来源**。

设计要点：
1. 以 ``NL2SQLState`` 为 schema，复用 ``main_graph`` 中现有的 6 个节点工厂
   （``make_ir_node`` 等），不重复实现 Agent 子图调用胶水。
2. fail-fast 早退用条件边 + END 表达（无 schema / 不可回答 / 无候选 → END），
   不依赖 Python ``if + return``。
3. history_cache 命中短路：入口条件边 ``cache_hit==True`` → ``execution``，
   跳过 ir/ss/cg。
4. 子图 END 时返回当前 partial ``NL2SQLState``，调用方据 ``final_sql`` /
   ``decision_path`` / ``rejection_reason`` / ``error`` 判定成败。

设计见 openspec/changes/refactor-single-query-graph/。
"""

from typing import Any

from langgraph.graph import END, START, StateGraph

from src.graph.main_graph import (
    _wrap_node,
    make_answerability_check_node,
    make_cg_node,
    make_decision_node,
    make_execution_node,
    make_ir_node,
    make_schema_finalize_node,
    make_ss_node,
)
from src.graph.state import NL2SQLState


def build_single_query_graph(
    retriever,
    selector,
    generator,
    fix_loop,
    decider,
    answerability_checker=None,
    data_dir: str = None,
):
    """构造并编译单查询流水线图。

    Args:
        retriever: InformationRetrieval 实例
        selector: SchemaSelector 实例
        generator: SQLGenerator 实例
        fix_loop: SQLFixLoop 实例
        decider: SelfConsistencyDecision 实例
        answerability_checker: AnswerabilityChecker 实例（可选；None 时不启用该阶段）
        data_dir: data/ 根目录（可选；定位 schema_graphs/，供 schema_finalize 用）。
            None 时 schema_finalize 内部取默认路径。

    Returns:
        CompiledGraph：以 ``NL2SQLState`` 为输入/输出，run_name="single-query"
    """
    graph = StateGraph(NL2SQLState)

    # ---- 节点（复用 main_graph 节点工厂 + _wrap_node 装饰，SSE/qid 日志照常）----
    graph.add_node("ir", _wrap_node("ir", make_ir_node(retriever)))
    graph.add_node("ss", _wrap_node("ss", make_ss_node(selector)))
    # schema_finalize（relocate-join-path-injection）：SS 之后、answerability_check/cg 之前
    graph.add_node("schema_finalize", _wrap_node("schema_finalize", make_schema_finalize_node(retriever, data_dir=data_dir)))
    if answerability_checker is not None:
        graph.add_node(
            "answerability_check",
            _wrap_node("answerability_check", make_answerability_check_node(answerability_checker)),
        )
    graph.add_node("cg", _wrap_node("cg", make_cg_node(generator)))
    graph.add_node("execution", _wrap_node("execution", make_execution_node(fix_loop)))
    graph.add_node("decision", _wrap_node("decision", make_decision_node(decider, fix_loop=fix_loop)))

    # ---- 入口：history_cache 命中短路（跳过 ir/ss/cg 直奔 execution）----
    def route_start(state: NL2SQLState) -> str:
        return "execution" if state.get("cache_hit", False) else "ir"

    graph.add_conditional_edges(START, route_start, {"ir": "ir", "execution": "execution"})

    # ---- ir → ss → schema_finalize（固定边）----
    graph.add_edge("ir", "ss")
    graph.add_edge("ss", "schema_finalize")

    # ---- schema_finalize 后：无 schema → END；有 schema → answerability_check（启用时）/ cg ----
    # 注：ss 未选出 schema 时，schema_finalize 也收不到有效 schema，故 fail-fast 仍在此守卫。
    if answerability_checker is not None:
        def route_after_schema_finalize(state: NL2SQLState) -> str:
            return "answerability_check" if state.get("selected_schema") else END

        graph.add_conditional_edges(
            "schema_finalize", route_after_schema_finalize,
            {"answerability_check": "answerability_check", END: END},
        )

        # answerability_check 后：false → END（拒答），否则 → cg
        def route_after_answerability(state: NL2SQLState) -> str:
            check = state.get("answerability_result")
            if check and check.get("answerable") == "false":
                return END
            return "cg"

        graph.add_conditional_edges(
            "answerability_check", route_after_answerability,
            {"cg": "cg", END: END},
        )
    else:
        def route_after_schema_finalize(state: NL2SQLState) -> str:
            return "cg" if state.get("selected_schema") else END

        graph.add_conditional_edges("schema_finalize", route_after_schema_finalize, {"cg": "cg", END: END})

    # ---- cg 后：无候选 → END，否则 → execution ----
    def route_after_cg(state: NL2SQLState) -> str:
        return "execution" if state.get("sql_candidates") else END

    graph.add_conditional_edges("cg", route_after_cg, {"execution": "execution", END: END})

    # ---- execution → decision → END ----
    graph.add_edge("execution", "decision")
    graph.add_edge("decision", END)

    return graph.compile().with_config(run_name="single-query")

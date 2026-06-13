"""
Execution Agent 子图

依据 §18.6：execute → (条件分支：成功→END / 失败且未超限→llm_fix→execute)
"""

from typing import Any, Dict, List, TypedDict

from langgraph.graph import END, START, StateGraph


class ExecutionGraphState(TypedDict, total=False):
    """Execution 子图内部状态"""

    sql: str                     # 当前要执行的 SQL（初始 + 每轮修正）
    original_sql: str            # 输入的原始 SQL（保留追溯）
    user_query: str
    schema_text: str

    attempt: int                 # 已尝试次数（0 表示首次执行）
    result: Any                  # ExecutionResult
    fix_history: List[str]       # 每次修正后的 SQL


def build_execution_graph(fix_loop):
    """
    构建 Execution Agent 子图

    Args:
        fix_loop: SQLFixLoop 实例（含 executor 和 llm_client）

    Returns:
        CompiledGraph
    """

    def node_execute(state: ExecutionGraphState) -> Dict[str, Any]:
        """执行当前 SQL"""
        result = fix_loop.executor.execute(state["sql"])
        return {
            "result": result,
            "attempt": state.get("attempt", 0) + 1,
        }

    def node_llm_fix(state: ExecutionGraphState) -> Dict[str, Any]:
        """调用 LLM 修正 SQL"""
        result = state.get("result")
        error = result.error if result else None
        fixed = fix_loop._try_fix(
            state["sql"], error,
            state.get("user_query", ""), state.get("schema_text", ""),
        )
        if fixed and fixed != state["sql"]:
            history = list(state.get("fix_history", []))
            history.append(fixed)
            return {"sql": fixed, "fix_history": history}
        # 无法修正：保持原 SQL，路由器会判定退出
        return {}

    def route_after_execute(state: ExecutionGraphState) -> str:
        """条件路由：成功 → END；失败且仍有重试机会 → llm_fix；否则 END"""
        result = state.get("result")
        if result is not None and result.success:
            return END
        if state.get("attempt", 0) > fix_loop.max_retries:
            return END
        if fix_loop.llm_client is None:
            return END
        return "llm_fix"

    def route_after_fix(state: ExecutionGraphState) -> str:
        """修正后若 sql 有更新则重试，否则结束"""
        # 通过判断 fix_history 末尾是否等于当前 sql 来确认 LLM 是否真的换了 SQL
        history = state.get("fix_history", [])
        if history and history[-1] == state.get("sql"):
            return "execute"
        return END

    graph = StateGraph(ExecutionGraphState)
    graph.add_node("execute", node_execute)
    graph.add_node("llm_fix", node_llm_fix)

    graph.add_edge(START, "execute")
    graph.add_conditional_edges(
        "execute",
        route_after_execute,
        {"llm_fix": "llm_fix", END: END},
    )
    graph.add_conditional_edges(
        "llm_fix",
        route_after_fix,
        {"execute": "execute", END: END},
    )

    return graph.compile().with_config(run_name="execution-graph")

"""
SS Agent 子图

依据 §18.4：to_mschema → evaluate_relevance → filter_columns
"""

from typing import Any, Dict, List, TypedDict

from langgraph.graph import END, START, StateGraph


class SSGraphState(TypedDict, total=False):
    """SS 子图内部状态"""

    user_query: str
    retrieved_context: Any              # RetrievedContext
    mschema_tables: List[Any]           # List[MSchemaTable]
    selected_schema: List[Any]          # List[MSchemaTable]，最终产物


def build_ss_graph(selector):
    """
    构建 SS Agent 子图

    Args:
        selector: SchemaSelector 实例

    Returns:
        CompiledGraph
    """

    def node_to_mschema(state: SSGraphState) -> Dict[str, Any]:
        tables = selector.to_mschema(state["retrieved_context"])
        return {"mschema_tables": tables}

    def node_evaluate_relevance(state: SSGraphState) -> Dict[str, Any]:
        tables = selector.evaluate_column_relevance(
            state["mschema_tables"], state["user_query"]
        )
        return {"mschema_tables": tables}

    def node_filter_columns(state: SSGraphState) -> Dict[str, Any]:
        filtered = selector.filter_columns(state["mschema_tables"])
        return {"selected_schema": filtered}

    graph = StateGraph(SSGraphState)
    graph.add_node("to_mschema", node_to_mschema)
    graph.add_node("evaluate_relevance", node_evaluate_relevance)
    graph.add_node("filter_columns", node_filter_columns)

    graph.add_edge(START, "to_mschema")
    graph.add_edge("to_mschema", "evaluate_relevance")
    graph.add_edge("evaluate_relevance", "filter_columns")
    graph.add_edge("filter_columns", END)

    return graph.compile().with_config(run_name="ss-graph")

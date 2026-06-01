"""
IR Agent 子图

依据 §18.3：
- 节点：extract_keywords → (retrieve_values || retrieve_schema) → enhance_with_schema
- LangGraph 1.x 暂不支持「真并行 fork」（StateGraph 是 DAG，节点同步执行），
  这里以串行表达：先 values，再 schema，再 enhance。如需并行可拆 Send API，
  本期保持简单串行；性能瓶颈在 LLM 不在 IR 内部步骤间。

子图对外暴露 `build_ir_graph(retriever)`，由 InformationRetrieval.build_graph()
调用。
"""

from typing import Any, Dict, List, Optional, TypedDict

from langgraph.graph import END, START, StateGraph


class IRGraphState(TypedDict, total=False):
    """IR 子图内部状态"""

    user_query: str
    database_filter: Optional[str]
    keywords: List[str]
    values: List[Any]              # List[RetrievedItem]
    schema_tables: List[Any]
    schema_columns: List[Any]
    retrieved_context: Any         # 最终 RetrievedContext


def build_ir_graph(retriever):
    """
    构建 IR Agent 子图

    Args:
        retriever: InformationRetrieval 实例（提供 extract_keywords/retrieve_values/
                   retrieve_schema/enhance_with_schema 等方法）

    Returns:
        CompiledGraph: 已编译的 LangGraph 子图
    """
    # 延迟导入避免循环依赖
    from src.retrieval.information_retrieval import RetrievedContext

    def node_extract_keywords(state: IRGraphState) -> Dict[str, Any]:
        """节点：关键词提取"""
        kws = retriever.extract_keywords(state["user_query"])
        return {"keywords": kws}

    def node_retrieve_values(state: IRGraphState) -> Dict[str, Any]:
        """节点：LSH 值检索"""
        keywords = state.get("keywords", [])
        values = retriever.retrieve_values(keywords)
        return {"values": values}

    def node_retrieve_schema(state: IRGraphState) -> Dict[str, Any]:
        """节点：语义 schema 检索"""
        schema = retriever.retrieve_schema(
            state["user_query"], state.get("database_filter")
        )
        return {
            "schema_tables": schema.get("tables", []),
            "schema_columns": schema.get("columns", []),
        }

    def node_assemble_and_enhance(state: IRGraphState) -> Dict[str, Any]:
        """节点：装配 RetrievedContext + 反推表覆盖"""
        ctx = RetrievedContext(
            tables=state.get("schema_tables", []),
            columns=state.get("schema_columns", []),
            values=state.get("values", []),
            keywords=state.get("keywords", []),
            lsh_hit_count=len(state.get("values", [])),
            vector_top_scores=[c.score for c in state.get("schema_columns", [])],
        )
        ctx = retriever.enhance_with_schema(ctx)
        return {"retrieved_context": ctx}

    graph = StateGraph(IRGraphState)
    graph.add_node("extract_keywords", node_extract_keywords)
    graph.add_node("retrieve_values", node_retrieve_values)
    graph.add_node("retrieve_schema", node_retrieve_schema)
    graph.add_node("assemble", node_assemble_and_enhance)

    graph.add_edge(START, "extract_keywords")
    graph.add_edge("extract_keywords", "retrieve_values")
    graph.add_edge("retrieve_values", "retrieve_schema")
    graph.add_edge("retrieve_schema", "assemble")
    graph.add_edge("assemble", END)

    return graph.compile()

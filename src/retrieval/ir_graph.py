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
    keywords: List[Any]            # List[KeywordGroup]
    flat_terms: List[str]          # 扁平化后的全部检索词（供 LSH 值检索使用）
    values: List[Any]              # List[RetrievedItem]
    schema_tables: List[Any]
    schema_columns: List[Any]
    keyword_columns_map: Dict[str, List[str]]
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
        """节点：关键词提取（Rewrite 前置后，query 已是完整语义，无需会话历史）

        extract_keywords 返回 List[KeywordGroup]，每组包含 phrase + terms（扁平化的同义词）。
        这里同时输出扁平化的 flat_terms 供 LSH 值检索使用。
        """
        keyword_groups = retriever.extract_keywords(state["user_query"])
        flat_terms: List[str] = []
        for g in keyword_groups:
            flat_terms.extend(getattr(g, "terms", []) or [])
        return {"keywords": keyword_groups, "flat_terms": flat_terms}

    def node_retrieve_values(state: IRGraphState) -> Dict[str, Any]:
        """节点：LSH 值检索（用扁平化后的字符串列表）"""
        flat_terms = state.get("flat_terms", [])
        values = retriever.retrieve_values(flat_terms)
        return {"values": values}

    def node_retrieve_schema(state: IRGraphState) -> Dict[str, Any]:
        """节点：语义 schema 检索（按 KeywordGroup 分组独立召回）"""
        keyword_groups = state.get("keywords", [])
        group_results = retriever.retrieve_schema(
            keyword_groups,
            database_filter=state.get("database_filter"),
        )

        # 跨组汇总：合并所有列，去重保留最高分；同时构建 keyword→columns 映射
        seen_columns: Dict[str, Any] = {}
        keyword_columns_map: Dict[str, List[str]] = {}
        for phrase, cols in (group_results or {}).items():
            col_keys: List[str] = []
            for col in cols:
                col_key = f"{col.table_name}.{col.name}"
                col_keys.append(col_key)
                if col_key not in seen_columns or col.score > seen_columns[col_key].score:
                    seen_columns[col_key] = col
            keyword_columns_map[phrase] = col_keys

        all_columns = sorted(seen_columns.values(), key=lambda c: c.score, reverse=True)

        # 从列推导表
        from src.retrieval.information_retrieval import RetrievedItem
        seen_tables = set()
        all_tables: List[Any] = []
        for col in all_columns:
            if col.table_name and col.table_name not in seen_tables:
                seen_tables.add(col.table_name)
                all_tables.append(RetrievedItem(
                    item_type="table",
                    name=col.table_name,
                    table_name=col.table_name,
                    score=col.score,
                    metadata={"database": col.metadata.get("database", "") if col.metadata else ""},
                ))

        return {
            "schema_tables": all_tables,
            "schema_columns": all_columns,
            "keyword_columns_map": keyword_columns_map,
        }

    def node_assemble_and_enhance(state: IRGraphState) -> Dict[str, Any]:
        """节点：装配 RetrievedContext + 反推表覆盖 + 注入 JOIN 路径"""
        keyword_groups = state.get("keywords", [])
        ctx = RetrievedContext(
            tables=state.get("schema_tables", []),
            columns=state.get("schema_columns", []),
            values=state.get("values", []),
            keywords=state.get("flat_terms", []),
            keyword_groups=keyword_groups,
            keyword_columns_map=state.get("keyword_columns_map", {}),
            lsh_hit_count=len(state.get("values", [])),
            vector_top_scores=[c.score for c in state.get("schema_columns", [])[:10]],
        )
        ctx = retriever.enhance_with_schema(ctx)
        # JOIN 路径注入已迁移到 schema_finalize 节点（SS→CG 之间），
        # 见 relocate-join-path-injection / schema_graph_builder.enrich_schema_with_join_paths。
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

    return graph.compile().with_config(run_name="ir-graph")

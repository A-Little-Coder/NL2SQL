"""
CG Agent 子图

依据 §18.5：extract_entities → mask_query → select_few_shot → llm_generate → safety_validate

为避免重写 SQLGenerator.generate() 内部，本子图将 generate() 拆解为
五个节点，节点直接调用 SQLGenerator 的细粒度方法。LLM 生成 + 安全验证
合并为一个 llm_generate_and_validate 节点（这两步在原实现中本就紧耦合）。
"""

import uuid
from typing import Any, Dict, List, TypedDict

from langgraph.graph import END, START, StateGraph


class CGGraphState(TypedDict, total=False):
    """CG 子图内部状态"""

    user_query: str
    selected_schema: List[Any]      # List[MSchemaTable]
    entities: List[str]
    masked_query: str
    few_shots: List[Dict[str, str]]
    sql_candidates: List[Any]       # List[SQLCandidate]
    query_preferences: Dict[str, str]  # 用户查询偏好（由主图注入）
    metric_definitions: List[Dict[str, Any]]  # 用户指标定义（由主图注入）


def build_cg_graph(generator):
    """
    构建 CG Agent 子图

    Args:
        generator: SQLGenerator 实例

    Returns:
        CompiledGraph
    """
    from src.schema_selection.schema_selector import MSchemaFormat
    from src.sql_generation.sql_generator import SQLCandidate, SQLStatus

    def node_extract_entities(state: CGGraphState) -> Dict[str, Any]:
        ents = generator.extract_entities(state["user_query"])
        return {"entities": ents}

    def node_mask_query(state: CGGraphState) -> Dict[str, Any]:
        masked = generator.mask_query(state["user_query"], state.get("entities", []))
        return {"masked_query": masked}

    def node_select_few_shot(state: CGGraphState) -> Dict[str, Any]:
        schema = state.get("selected_schema", [])
        is_multi = len(schema) > 1
        shots = generator.select_few_shot_examples(
            state.get("masked_query", ""), schema, is_multi
        )
        return {"few_shots": shots}

    def node_llm_generate_and_validate(state: CGGraphState) -> Dict[str, Any]:
        """LLM 多候选生成 + sqlglot 安全验证（紧耦合，合并为一个节点）"""
        if not generator.llm_client:
            return {"sql_candidates": []}

        schema = state.get("selected_schema", [])
        try:
            mschema_dict = MSchemaFormat.create_mschema_schema(schema)
            schema_text = MSchemaFormat.format_for_llm(mschema_dict)

            # 注入用户偏好
            user_prefs = state.get("query_preferences", {}) or {}
            pref_lines = []
            if user_prefs.get("default_sort"):
                pref_lines.append(f"- 排序偏好：默认使用 {user_prefs['default_sort']}")
            if user_prefs.get("default_limit"):
                pref_lines.append(f"- 默认限制行数：{user_prefs['default_limit']}")
            if user_prefs.get("default_group_by"):
                pref_lines.append(f"- 分组偏好：{user_prefs['default_group_by']}")
            preference_text = "\n".join(pref_lines) if pref_lines else ""

            # 注入指标定义（高置信度）
            metrics = state.get("metric_definitions", []) or []
            metric_lines = []
            for m in metrics:
                if m.get("confidence", 0) >= 0.8:
                    metric_lines.append(f"- {m.get('name', '')}: {m.get('description', '')} → {m.get('sql_pattern', '')}")
            metric_text = "\n".join(metric_lines) if metric_lines else ""

            from src.sql_generation.prompts import SQL_GENERATION_PROMPT
            from utils.llm_client import parse_json, stream_with_sse
            from langchain_core.messages import HumanMessage

            base_messages = SQL_GENERATION_PROMPT.format_messages(
                user_query=state["user_query"],
                schema_text=schema_text,
                num_candidates=generator.num_candidates,
            )
            # 把额外上下文（preference / metric）作为追加的 user 消息（保持模板纯净）
            if preference_text:
                base_messages.append(
                    HumanMessage(f"## 用户查询偏好\n{preference_text}")
                )
            if metric_text:
                base_messages.append(HumanMessage(
                    f"## 已知指标定义\n{metric_text}\n"
                    "如果当前查询匹配以上指标定义，请优先使用对应的 SQL 模式。"
                ))
            raw = stream_with_sse(generator.llm_client.stream(base_messages, as_json=True, temperature=0.3))
            result = parse_json(raw)

            candidates: List[SQLCandidate] = []
            for entry in result.get("candidates", []):
                sql = entry.get("sql", "").strip()
                if not sql:
                    continue
                is_valid, msg = generator.validator.validate(sql)
                if not is_valid:
                    continue
                candidates.append(SQLCandidate(
                    id=str(uuid.uuid4())[:8],
                    sql=sql,
                    status=SQLStatus.VALIDATED,
                    generation_reason=entry.get("reason", ""),
                ))
            return {"sql_candidates": candidates[: generator.num_candidates]}
        except Exception:
            return {"sql_candidates": []}

    graph = StateGraph(CGGraphState)
    graph.add_node("extract_entities", node_extract_entities)
    graph.add_node("mask_query", node_mask_query)
    graph.add_node("select_few_shot", node_select_few_shot)
    graph.add_node("llm_generate_and_validate", node_llm_generate_and_validate)

    graph.add_edge(START, "extract_entities")
    graph.add_edge("extract_entities", "mask_query")
    graph.add_edge("mask_query", "select_few_shot")
    graph.add_edge("select_few_shot", "llm_generate_and_validate")
    graph.add_edge("llm_generate_and_validate", END)

    return graph.compile().with_config(run_name="cg-graph")

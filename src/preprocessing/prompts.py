# ============================================================================
# Preprocessing 模块的 Prompt 模板（OpenSpec migrate-llm-client-to-langchain）
# ============================================================================
# 集中管理 preprocessing 中的 LLM prompt（离线场景）：
#   - JOIN_INFER_PROMPT  推断两个表之间的 JOIN 关系
# ============================================================================

from langchain_core.prompts import ChatPromptTemplate


JOIN_INFER_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "你是数据库专家，只输出 JSON。"),
    ("user", """请判断以下两个表之间是否存在 JOIN 关系。

表A:
{desc_a}

表B:
{desc_b}

如果存在 JOIN 关系，请返回 JSON 格式：
{{"has_join": true, "join_keys": [["{table_a}.列名", "{table_b}.列名"]]}}

如果没有明显的 JOIN 关系，返回：
{{"has_join": false, "join_keys": []}}

注意：
1. 只返回确定的 JOIN 关系，不要猜测
2. 可以有多个 join_key（如同时通过 id 和 name 关联）
3. 只输出 JSON，不要解释"""),
])

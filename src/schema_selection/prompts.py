# ============================================================================
# Schema Selection 模块的 Prompt 模板（OpenSpec migrate-llm-client-to-langchain）
# ============================================================================
# 集中管理 schema_selector.py 中的 LLM prompt：
#   - COLUMN_RELEVANCE_PROMPT  列相关性评估
# ============================================================================

from langchain_core.prompts import ChatPromptTemplate


COLUMN_RELEVANCE_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "你是 SQL 专家，只输出 JSON。"),
    ("user", """请评估以下 schema 中每个列与用户查询的相关性。

用户查询: "{user_query}"

可用 schema:
{schema_text}

请为每个列评分（0.0 - 1.0）：
- 1.0: 直接用于 SELECT、WHERE 或 GROUP BY
- 0.7: JOIN 或聚合时需要
- 0.5: 间接相关
- 0.3: 弱相关，可能不需要
- 0.0: 完全不相关

只返回 JSON，格式如下：
{{"scores": [
  {{"table": "表名", "column": "列名", "score": 0.9, "reason": "原因"}},
  ...
]}}

注意：必须为 schema 中**每一个**列打分。"""),
])

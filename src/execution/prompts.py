# ============================================================================
# Execution 模块的 Prompt 模板（OpenSpec migrate-llm-client-to-langchain）
# ============================================================================
# 集中管理 executor.py 中的 LLM prompt：
#   - SQL_FIX_PROMPT  SmartFix 修复 prompt（带 fix_history 上下文）
# ============================================================================

from langchain_core.prompts import ChatPromptTemplate


SQL_FIX_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "你是 SQL 修正专家，只输出 JSON。"),
    ("user", """下面的 SQL 执行失败了，请修正它。

原始用户查询: "{user_query}"

失败的 SQL:
{sql}

错误信息:
{error_info}

可用 Schema:
{schema_text}
{fix_history_section}
请生成修正后的 SQL（只生成 SELECT 查询，禁止修改数据），返回 JSON：
{{"sql": "修正后的SQL", "reason": "修正理由"}}"""),
])

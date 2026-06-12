# ============================================================================
# SQL Generation 模块的 Prompt 模板（OpenSpec migrate-llm-client-to-langchain）
# ============================================================================
# 集中管理 sql_generator.py / cg_graph.py 中的 LLM prompt：
#   - SQL_GENERATION_PROMPT    生成 SQL 候选
#   - CG_REFINE_PROMPT         子图重写阶段（如有）
# ============================================================================

from langchain_core.prompts import ChatPromptTemplate


SQL_GENERATION_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "你是 SQL 专家，只输出 JSON。"),
    ("user", """请根据以下 schema 和用户查询生成 SQLite SQL 语句。

用户查询: "{user_query}"

数据库 Schema:
{schema_text}

要求:
1. 只生成 SELECT 查询，不要生成 INSERT/UPDATE/DELETE/DROP 等修改操作
2. SQL 必须兼容 SQLite 语法
3. 如果涉及多表，使用合适的 JOIN
4. 考虑 WHERE 条件、GROUP BY、ORDER BY、LIMIT 等子句
5. 生成 {num_candidates} 个不同的 SQL 候选（可以用不同思路实现）
6. 返回 JSON 格式：{{"candidates": [{{"sql": "SQL语句", "reason": "生成理由"}}, ...]}}

请生成 SQL:"""),
])


CG_SQL_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "你是 SQL 专家，只输出 JSON。"),
    ("user", """请根据以下 schema 和用户查询生成 SQLite SQL 语句。

用户查询: "{user_query}"

数据库 Schema:
{schema_text}

要求:
1. 只生成 SELECT 查询
2. SQL 必须兼容 SQLite 语法
3. 返回 JSON 格式：{{"sql": "SQL语句"}}"""),
])
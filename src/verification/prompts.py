# ============================================================================
# Verification 模块的 Prompt 模板（OpenSpec migrate-llm-client-to-langchain）
# ============================================================================
# 集中管理 answerability.py / result_verifier.py 中的 LLM prompt：
#   - ANSWERABILITY_CHECK_PROMPT  数据库可回答性判断
#   - RESULT_VERIFICATION_PROMPT  结果可信度验证（最后兜底）
# ============================================================================

from langchain_core.prompts import ChatPromptTemplate


ANSWERABILITY_CHECK_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "你是数据库可回答性判断专家，只输出 JSON。"),
    ("user", """根据用户问题和当前可用的数据库 schema，判断数据库是否有足够信息回答该问题。

## 宽松原则
- 只要有**合理的可能性**能回答，就判为 "true" 或 "uncertain"
- 只有在**明确**缺少关键实体、或数据粒度与问题要求**严重不匹配**时，才判为 "false"
- "uncertain" 一律视为可继续，不要过度拦截

## 判断维度
1. **实体覆盖**：问题中提到的核心实体（如"学生""订单"）在 schema 中是否有对应表/列
2. **粒度匹配**：数据的粒度是否与问题一致（如问"每个学生"但数据是"每个学校"级别 → 不匹配）
3. **字段覆盖**：问题请求的维度（如"姓名+分数"）是否在 schema 中有对应列

## 用户问题
{user_query}

## 可用数据库 Schema
{schema_text}

## IR 检索辅助信息
- 提取关键词: {keywords}
- LSH 值命中数: {lsh_hit_count}
- 向量检索 top 分数: {vector_top_scores}

请判断并返回 JSON：
{{
  "answerable": "true" | "false" | "uncertain",
  "confidence": 0.0-1.0,
  "reason": "判断理由",
  "missing_info": "缺少什么信息（如果 false/uncertain，否则空字符串）",
  "granularity_match": "粒度是否匹配的说明"
}}"""),
])


RESULT_VERIFICATION_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "你是 SQL 结果验证专家，只输出 JSON。"),
    ("user", """请严格验证：生成的 SQL 是否**真正在回答用户的问题**，而非答非所问。

## 严格原则
- 只有当 SQL 语义与用户问题**明确对齐**时才判 "true"
- 存在**任何**粒度不匹配、硬凑替代、维度缺失时判 "false"
- 答非所问比拒答对用户伤害更大——拒答至少诚实，答非所问会误导

## 检查维度
1. **粒度匹配**：SQL 查询的粒度是否与问题匹配（如问"每个学生"但 SQL 查的是"每个学校"→ 不匹配）
2. **维度覆盖**：结果列是否覆盖了问题中请求的维度（如问"姓名+分数"但结果只有分数）
3. **硬凑检测**：是否存在用近似字段替代了用户真正要的字段（如用"School"替代"学生姓名"）

## 用户问题
{user_query}

## 最终选定的 SQL
{selected_sql}

## SQL 执行结果样本（列名 + 前 5 行）
{result_sample}

## 数据库 Schema（参考）
{schema_text}

请验证并返回 JSON：
{{
  "trustworthy": "true" | "false",
  "reason": "验证理由",
  "granularity_match": "粒度对齐说明",
  "semantic_alignment": "语义对齐说明"
}}"""),
])

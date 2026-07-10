# ============================================================================
# Memory 模块的 Prompt 模板（OpenSpec migrate-llm-client-to-langchain）
# ============================================================================
# 集中管理 memory_updater.py / history_cache.py 中的 LLM prompt：
#   - METRIC_EXTRACT_PROMPT   从 SQL 提取业务指标定义（离线场景）
#   - CACHE_CHECK_PROMPT      历史命中检测（查是否可复用历史 SQL）
# ============================================================================

from langchain_core.prompts import ChatPromptTemplate


METRIC_EXTRACT_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "你是一个 SQL 分析器。只输出 JSON。"),
    ("user", """从以下 SQL 中提取业务指标定义。

SQL:
{sql}

请输出 JSON:
{{
    "metric_name": "指标名称（中文，如'销售额'）",
    "description": "指标的业务含义描述",
    "sql_pattern": "简化后的 SQL 模式（保留聚合函数和 WHERE 条件）"
}}

如果 SQL 不是简单聚合查询（无 SUM/COUNT/AVG/MAX/MIN），返回 {{"metric_name": null}}。"""),
])


CACHE_CHECK_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "你是一个历史命中检测器。只输出 JSON。"),
    ("user", """你是一个 NL2SQL 系统的历史命中检测器。请判断当前用户查询是否可以直接复用历史记录中的 SQL。

## 当前查询
{user_query}

## 历史对话（最近 {max_turns} 轮）
{conversation_history}

## 已知指标定义
{metric_definitions}

## 判断规则
1. 如果当前查询与历史某轮查询意图等价（意图相同、参数相同）→ 复用该轮 SQL
2. 如果当前查询可以用已知指标定义直接回答 → 使用指标定义的 sql_pattern
3. 如果当前查询仅值参数变化（如 WHERE 谓词值/地区/产品/阈值/LIMIT/HAVING 值变化，意图与结构不变）→ 仍可复用，值参数差异交由后续值改写阶段处理
4. 如果当前查询是上一轮的 follow-up 但意图不同 / 结构变化（增删 WHERE 谓词/GROUP BY 维度/ORDER BY/聚合方式/表/JOIN）→ 不复用
5. 置信度低于 0.8 时请返回 false

## 输出格式（仅 JSON）
{{
    "can_reuse": true/false,
    "source": "session_history" 或 "metric_definition" 或 null,
    "cached_sql": "复用的 SQL" 或 null,
    "confidence": 0.0-1.0,
    "matched_turn_index": "命中的历史轮次索引（从历史对话中的「轮次 X」提取，无命中时为 null）",
    "reason": "判断理由"
}}"""),
])


VALUE_REWRITE_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "你是一个 SQL 值参数改写器。只输出 JSON。"),
    ("user", """你是一个 SQL 值参数改写器。请比对历史查询与当前查询的值参数引用，仅改写 cached_sql 中已存在的值参数以对齐当前查询的意图，不要改动 SQL 的其他部分。

## 历史查询
{historical_query}

## 当前查询
{user_query}

## 待改写的 SQL
{cached_sql}

## 任务说明
1. 比对历史查询与当前查询的值参数差异（如 WHERE 谓词值/地区/产品/阈值/LIMIT/HAVING 值等）
2. 仅改写 SQL 中已存在的值参数（如 region='华东'、LIMIT 10、year=2024、HAVING sum>1000 等）
3. 绝不改动 SQL 结构（表名、字段名、聚合方式、GROUP BY/ORDER BY/JOIN、其他非值参数部分）
4. 绝不增删 WHERE 谓词或其他子句
5. 如果两查询值参数一致，或无 historical_query，或值非字面量难以改写，或无需改写，则原样返回 cached_sql
6. 如果无法确定如何改写，也原样返回 cached_sql

## 输出格式（仅 JSON）
{{
    "adjusted_sql": "改写后的 SQL，或原样返回的 cached_sql",
    "changed": true/false,
    "reason": "改写理由或原样返回原因"
}}"""),
])
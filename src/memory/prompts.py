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
1. 如果当前查询与历史某轮查询完全等价（意图相同、参数相同）→ 复用该轮 SQL
2. 如果当前查询可以用已知指标定义直接回答 → 使用指标定义的 sql_pattern
3. 如果当前查询涉及时间范围变化（如"昨天的"→"今天的"、"去年"→"今年"）→ 不复用
4. 如果当前查询是上一轮的 follow-up 但意图不同 → 不复用
5. 置信度低于 0.8 时请返回 false

## 输出格式（仅 JSON）
{{
    "can_reuse": true/false,
    "source": "session_history" 或 "metric_definition" 或 null,
    "cached_sql": "复用的 SQL" 或 null,
    "confidence": 0.0-1.0,
    "reason": "判断理由"
}}"""),
])
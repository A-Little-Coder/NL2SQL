"""
记忆自动学习（MemoryUpdater）

每次主图执行完成后，从本轮结果中提取信息，自动更新用户记忆和会话记忆。

自动学习内容：
  1. 常用表 — 从 final_sql 中提取表名
  2. 指标定义 — 检测简单聚合 SQL，提取指标名 → SQL 模式映射
  3. 查询偏好 — 统计时间范围/排序/limit 等偏好
  4. 会话上下文摘要 — 更新 last_topic/last_tables/last_time_range
"""

import json
import re
from typing import Any, Dict, List, Optional


# 简单聚合 SQL 检测正则
AGG_PATTERN = re.compile(
    r"\b(SELECT\s+(SUM|COUNT|AVG|MAX|MIN|ROUND)\s*\()",
    re.IGNORECASE,
)

# SQL 表名提取正则
TABLE_PATTERN = re.compile(
    r"\b(FROM|JOIN)\s+(\w+)",
    re.IGNORECASE,
)

# LLM 指标提取 Prompt
METRIC_EXTRACT_PROMPT = """你是一个 SQL 分析器。请从以下 SQL 中提取业务指标定义。

SQL:
{sql}

请输出 JSON:
{{
    "metric_name": "指标名称（中文，如'销售额'）",
    "description": "指标的业务含义描述",
    "sql_pattern": "简化后的 SQL 模式（保留聚合函数和 WHERE 条件）"
}}

如果 SQL 不是简单聚合查询（无 SUM/COUNT/AVG/MAX/MIN），返回 {{"metric_name": null}}。
"""


class MemoryUpdater:
    """记忆自动学习器"""

    def __init__(self, llm_client=None):
        self._llm = llm_client

    def update(
        self,
        user_memory,
        session_memory,
        state: Dict[str, Any],
    ):
        """
        执行所有记忆更新

        Args:
            user_memory: UserMemory 实例
            session_memory: SessionMemory 实例
            state: NL2SQLState 字典
        """
        # 1. 提取表名 → 更新常用表
        self._update_table_usage(user_memory, state)

        # 2. 检测聚合 SQL → 更新指标定义
        self._update_metric_definitions(user_memory, state)

        # 3. 更新查询偏好
        self._update_query_preferences(user_memory, state)

        # 4. 更新会话上下文摘要
        self._update_session_context(session_memory, state)

        # 5. 写入澄清历史
        self._update_clarification_history(user_memory, state)

    # ── 1. 常用表 ─────────────────────────────────────────────

    def _update_table_usage(self, user_memory, state: Dict[str, Any]):
        """从 SQL 中提取表名并更新常用表计数"""
        sql = state.get("final_sql", "") or ""
        if not sql:
            return

        tables = set()
        for match in TABLE_PATTERN.finditer(sql):
            tables.add(match.group(2).lower())

        for table in tables:
            user_memory.record_table_usage(table)

    # ── 2. 指标定义 ───────────────────────────────────────────

    def _update_metric_definitions(self, user_memory, state: Dict[str, Any]):
        """检测简单聚合 SQL 并提取指标定义"""
        sql = state.get("final_sql", "") or ""
        if not sql:
            return

        # 先检查是否包含聚合函数
        if not AGG_PATTERN.search(sql):
            return

        # 如果没配 LLM，用简单规则提取
        if self._llm is None:
            self._extract_metric_simple(user_memory, sql)
            return

        # 调 LLM 提取
        try:
            messages = [
                {"role": "system", "content": "你是一个 SQL 分析器。只输出 JSON。"},
                {"role": "user", "content": METRIC_EXTRACT_PROMPT.format(sql=sql)},
            ]
            response = self._llm.chat(messages, response_format={"type": "json_object"})
            data = json.loads(response) if isinstance(response, str) else response

            metric_name = data.get("metric_name")
            if metric_name:
                user_memory.record_metric_definition(
                    name=metric_name,
                    description=data.get("description", ""),
                    sql_pattern=data.get("sql_pattern", sql),
                    source="auto_learned",
                    confidence=0.5,
                )
        except Exception:
            # LLM 失败时回退到简单规则
            self._extract_metric_simple(user_memory, sql)

    def _extract_metric_simple(self, user_memory, sql: str):
        """简单规则提取指标名（无 LLM 时回退）"""
        match = AGG_PATTERN.search(sql)
        if not match:
            return

        agg_func = match.group(2).upper()
        # 提取聚合函数的参数列名
        col_match = re.search(rf"{agg_func}\s*\(\s*(\w+)", sql, re.IGNORECASE)
        if not col_match:
            return

        col_name = col_match.group(1)
        metric_name = f"{agg_func}_{col_name}"

        user_memory.record_metric_definition(
            name=metric_name,
            description=f"{agg_func} of {col_name}",
            sql_pattern=sql,
            source="auto_learned",
            confidence=0.5,
        )

    # ── 3. 查询偏好 ───────────────────────────────────────────

    def _update_query_preferences(self, user_memory, state: Dict[str, Any]):
        """从 SQL 中检测查询偏好"""
        sql = state.get("final_sql", "") or ""

        # 检测排序偏好
        if re.search(r"\bORDER\s+BY\s+\w+\s+DESC\b", sql, re.IGNORECASE):
            user_memory.update_query_preference("default_sort", "DESC")
        elif re.search(r"\bORDER\s+BY\s+\w+\s+ASC\b", sql, re.IGNORECASE):
            user_memory.update_query_preference("default_sort", "ASC")

        # 检测分组粒度
        if re.search(r"\bGROUP\s+BY\s+\w+\.?(year|month|day|date)", sql, re.IGNORECASE):
            user_memory.update_query_preference("default_group_by", "daily")

        # 检测 LIMIT
        limit_match = re.search(r"\bLIMIT\s+(\d+)", sql, re.IGNORECASE)
        if limit_match:
            limit = int(limit_match.group(1))
            if limit <= 20:
                user_memory.update_query_preference("default_limit", str(limit))

    # ── 4. 会话上下文摘要 ─────────────────────────────────────

    def _update_session_context(self, session_memory, state: Dict[str, Any]):
        """更新会话上下文摘要"""
        query = state.get("user_query", "") or ""
        sql = state.get("final_sql", "") or ""

        if not query:
            return

        # 从 SQL 中提取表名
        tables = set()
        for match in TABLE_PATTERN.finditer(sql):
            tables.add(match.group(2).lower())

        # 从查询中提取主题（取前 20 字作为主题）
        topic = query[:20] if len(query) > 20 else query

        # 检测时间范围
        time_range = None
        year_match = re.search(r"(\d{4})", query)
        if year_match:
            time_range = year_match.group(1)

        summary = {
            "last_topic": topic,
            "last_tables": list(tables),
        }
        if time_range:
            summary["last_time_range"] = time_range

        session_memory.update_context_summary(summary)

    # ── 5. 澄清历史 ───────────────────────────────────────────

    def _update_clarification_history(self, user_memory, state: Dict[str, Any]):
        """写入反问澄清历史到用户记忆"""
        clarification_history = state.get("clarification_history", []) or []
        for entry in clarification_history:
            user_memory.append_clarification(entry)

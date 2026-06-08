"""
历史命中检测（HistoryCache）

在 IR 之前判断当前查询是否与历史查询等价或可用已知指标直接回答。
命中时复用历史 SQL 重新执行，不复用历史 result。

安全边界：
  - confidence < 0.8 → 不复用，走完整链路
  - 涉及时间变化的 follow-up → 不复用（数据可能已变）
  - 只复用 SQL，不复用 result（重新执行保证时效）
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class CacheResult:
    """历史命中检测结果"""
    hit: bool = False
    cached_sql: Optional[str] = None
    source: Optional[str] = None  # "session_history" | "metric_definition"
    confidence: float = 0.0


# LLM 判断 Prompt 模板
CACHE_CHECK_PROMPT = """你是一个 NL2SQL 系统的历史命中检测器。请判断当前用户查询是否可以直接复用历史记录中的 SQL。

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
}}
"""


class HistoryCache:
    """历史命中检测器"""

    def __init__(self, llm_client, min_confidence: float = 0.8):
        self._llm = llm_client
        self.min_confidence = min_confidence

    def check(
        self,
        user_query: str,
        session_history: List[Dict[str, Any]],
        metric_definitions: List[Dict[str, Any]],
    ) -> CacheResult:
        """
        检测当前查询是否能命中历史缓存

        Args:
            user_query: 当前用户查询
            session_history: 最近 N 轮会话历史
            metric_definitions: 用户记忆中的指标定义

        Returns:
            CacheResult
        """
        if not session_history and not metric_definitions:
            return CacheResult(hit=False)

        # 构建 Prompt
        history_text = self._format_history(session_history)
        metrics_text = self._format_metrics(metric_definitions)

        prompt = CACHE_CHECK_PROMPT.format(
            user_query=user_query,
            max_turns=len(session_history),
            conversation_history=history_text or "无",
            metric_definitions=metrics_text or "无",
        )

        try:
            messages = [
                {"role": "system", "content": "你是一个历史命中检测器。只输出 JSON。"},
                {"role": "user", "content": prompt},
            ]
            response = self._llm.chat(messages, response_format={"type": "json_object"})
            result = self._parse_response(response)
        except Exception:
            # LLM 调用失败时安全降级：不走缓存
            return CacheResult(hit=False)

        # 安全边界检查
        if not result.hit:
            return result
        if result.confidence < self.min_confidence:
            return CacheResult(hit=False)
        if not result.cached_sql:
            return CacheResult(hit=False)

        return result

    def _format_history(self, history: List[Dict[str, Any]]) -> str:
        """将会话历史格式化为文本"""
        lines = []
        for turn in history[-5:]:  # 最多 5 轮
            idx = turn.get("turn_index", "?")
            query = turn.get("user_query", "")
            sql = turn.get("final_sql", "")
            lines.append(f"轮次 {idx}: \"{query}\" -> SQL: {sql}")
        return "\n".join(lines)

    def _format_metrics(self, metrics: List[Dict[str, Any]]) -> str:
        """将指标定义格式化为文本"""
        lines = []
        for m in metrics:
            name = m.get("name", "")
            desc = m.get("description", "")
            pattern = m.get("sql_pattern", "")
            lines.append(f"{name}({desc}): {pattern}")
        return "\n".join(lines)

    def _parse_response(self, response) -> CacheResult:
        """解析 LLM 返回的 JSON 结果"""
        try:
            if isinstance(response, str):
                import json
                data = json.loads(response)
            else:
                data = response

            return CacheResult(
                hit=data.get("can_reuse", False),
                cached_sql=data.get("cached_sql"),
                source=data.get("source"),
                confidence=data.get("confidence", 0.0),
            )
        except (ValueError, TypeError, KeyError):
            return CacheResult(hit=False)

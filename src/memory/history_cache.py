"""
历史命中检测（HistoryCache）

在 IR 之前判断当前查询是否与历史查询等价或可用已知指标直接回答。
命中时复用历史 SQL 重新执行，不复用历史 result。

安全边界：
  - confidence < 0.8 → 不复用，走完整链路
  - 涉及时间变化的 follow-up → 不复用（数据可能已变）
  - 只复用 SQL，不复用 result（重新执行保证时效）
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class CacheResult:
    """历史命中检测结果"""
    hit: bool = False
    cached_sql: Optional[str] = None
    source: Optional[str] = None  # "session_history" | "metric_definition"
    confidence: float = 0.0
    historical_query: Optional[str] = None  # 命中的历史 query（仅 source=session_history 时有效）
    matched_metric_name: Optional[str] = None  # 命中的指标名（仅 source=metric_definition 时有效，反查失败为 None）


# Prompt 已迁移至 src/memory/prompts.py
from src.memory.prompts import CACHE_CHECK_PROMPT
from src.memory.session_recall import HistoricalSQLReference


class HistoryCache:
    """历史命中检测器"""

    def __init__(self, llm_client, min_confidence: float = 0.8, session_retriever=None):
        self._llm = llm_client
        self.min_confidence = min_confidence
        self.session_retriever = session_retriever

    def recall_session_history(
        self,
        user_query: str,
        *,
        user_id: str,
        session_id: str,
        db_id: str,
    ) -> List[HistoricalSQLReference]:
        """召回当前 session 内的成功历史 query，异常时安全降级为空"""
        if self.session_retriever is None:
            return []
        try:
            return self.session_retriever.retrieve(
                user_query,
                user_id=user_id,
                session_id=session_id,
                db_id=db_id,
            )
        except Exception:
            return []
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

        messages = CACHE_CHECK_PROMPT.format_messages(
            user_query=user_query,
            max_turns=len(session_history),
            conversation_history=history_text or "无",
            metric_definitions=metrics_text or "无",
        )

        try:
            # 历史缓存检测属于"准实时"场景：直接走 invoke 而不是 stream
            # （响应快 + 不需要 SSE 推送 + 不需要思考链：规则明确，输出固定）
            data = self._llm.invoke(messages, as_json=True, thinking=False, run_name="cache-check")
            result = self._parse_response(data, session_history, metric_definitions)
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
            idx = turn.get("turn_index", turn.get("turn_id", "?"))
            query = turn.get("user_query", turn.get("historical_query", ""))
            sql = turn.get("final_sql", turn.get("historical_sql", ""))
            score = turn.get("rrf_score")
            suffix = f" | RRF: {score:.4f}" if isinstance(score, (int, float)) else ""
            lines.append(f"轮次 {idx}: \"{query}\" -> SQL: {sql}{suffix}")
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

    def _parse_response(self, response, session_history: List[Dict[str, Any]], metric_definitions: Optional[List[Dict[str, Any]]] = None) -> CacheResult:
        """解析 LLM 返回的 JSON 结果"""
        try:
            if isinstance(response, str):
                import json
                data = json.loads(response)
            else:
                data = response

            hit = data.get("can_reuse", False)
            source = data.get("source")
            matched_turn_index = data.get("matched_turn_index")

            # 从 session_history 中回填命中的历史 query
            # 优先用 matched_turn_index 匹配；不准/缺失时用 cached_sql 精确反查兜底
            # （LLM 返回的 matched_turn_index 在多候选时不稳定，cached_sql 字符串匹配更可靠）
            historical_query = None
            cached_sql = data.get("cached_sql")
            if hit and source == "session_history":
                if matched_turn_index is not None:
                    # 兼容 int/str 类型的 turn_index/turn_id
                    for turn in session_history:
                        idx = turn.get("turn_index", turn.get("turn_id"))
                        if idx is not None and str(idx) == str(matched_turn_index):
                            historical_query = turn.get("user_query", turn.get("historical_query"))
                            break
                if historical_query is None and cached_sql:
                    # 兜底：按 cached_sql 精确反查（忽略首尾空白与末尾分号差异）
                    norm = lambda s: str(s).strip().rstrip(";").strip()
                    for turn in session_history:
                        turn_sql = turn.get("final_sql", turn.get("historical_sql"))
                        if turn_sql and norm(turn_sql) == norm(cached_sql):
                            historical_query = turn.get("user_query", turn.get("historical_query"))
                            break

            # 命中 metric_definition 时，按 cached_sql 反查指标名（归一化匹配）
            # metric_definitions 是确定有限集，反查比 LLM 返回指标名更可靠；反查失败为 None
            matched_metric_name = None
            if hit and source == "metric_definition" and cached_sql:
                norm_metric = lambda s: str(s).strip().rstrip(";").strip().lower()
                for m in (metric_definitions or []):
                    pattern = m.get("sql_pattern")
                    if pattern and norm_metric(pattern) == norm_metric(cached_sql):
                        matched_metric_name = m.get("name")
                        break

            return CacheResult(
                hit=hit,
                cached_sql=data.get("cached_sql"),
                source=source,
                confidence=data.get("confidence", 0.0),
                historical_query=historical_query,
                matched_metric_name=matched_metric_name,
            )
        except (ValueError, TypeError, KeyError):
            return CacheResult(hit=False)

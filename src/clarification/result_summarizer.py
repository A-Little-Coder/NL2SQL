# ============================================================================
# ResultSummarizer：结果总结模块（决策 15）
# ============================================================================
# 职责：
#   1. 按需触发：单结果无表透传不调 LLM；多结果/有表才调 LLM 汇总
#   2. 数据表结构摘要降 token：仅取「列名 + 行数 + 前 5 行」喂 LLM，不喂整表
#   3. 多结果汇总：LLM 生成连贯自然语言，按子查询顺序组织，每个标注来源
#
# 设计依据（决策 15）：
#   - 原始完整结果通过 state 透传前端渲染，不进 LLM
#   - 结构摘要策略与现有 ResultVerifier（列名+前5行）思路一致
# ============================================================================

from typing import Any, Dict, List

from loguru import logger

from src.clarification.prompts import RESULT_SUMMARIZER_PROMPT
from utils.llm_client import stream_with_sse


# 头部样本行数（决策 15：前 5 行）
SAMPLE_ROWS = 5


class ResultSummarizer:
    """
    结果总结器（决策 15）

    Attributes:
        llm_client: LLM 客户端（None 时降级为简单拼接）
    """

    def __init__(self, llm_client=None):
        self.llm_client = llm_client

    def summarize(self, subquery_results: List[Dict[str, Any]], user_query: str) -> str:
        """
        汇总多个子查询结果为一段连贯自然语言

        Args:
            subquery_results: 子查询结果列表（SubqueryResult.to_dict()）
            user_query: 用户原始查询

        Returns:
            汇总文本
        """
        if not subquery_results:
            return ""

        # 构造每个子查询的结构摘要（降 token，决策 15）
        summaries = []
        for i, r in enumerate(subquery_results, 1):
            summaries.append(self._format_subquery_summary(i, r))

        subquery_summaries_text = "\n\n".join(summaries)

        # LLM 未设置 → 降级拼接
        if not self.llm_client:
            logger.warning("ResultSummarizer: LLM 未设置，降级拼接")
            return self._fallback(subquery_results)

        try:
            messages = RESULT_SUMMARIZER_PROMPT.format_messages(
                user_query=user_query,
                subquery_summaries=subquery_summaries_text,
            )
            raw = stream_with_sse(
                self.llm_client.stream(
                    messages,
                    as_json=False,  # 纯文本输出
                    temperature=0.3,
                    thinking=False,
                    run_name="result-summarizer",
                )
            )
            summary = raw.strip() if isinstance(raw, str) else str(raw).strip()
            if not summary:
                return self._fallback(subquery_results)
            return summary
        except Exception as e:
            logger.error(f"ResultSummarizer LLM 调用失败，降级拼接: {e}")
            return self._fallback(subquery_results)

    # ------------------------------------------------------------------
    # 数据表结构摘要（决策 15 核心：降 token）
    # ------------------------------------------------------------------
    @staticmethod
    def _format_subquery_summary(index: int, result: Dict[str, Any]) -> str:
        """把单个子查询结果格式化为结构摘要：列名 + 行数 + 前 5 行样本。

        ★ 关键：不把原始整表喂给 LLM，只取结构摘要，节约 token。
        原始完整结果已通过 state 透传前端渲染。
        """
        subq = result.get("subquery", "")
        success = result.get("success", False)
        sql = result.get("final_sql", "")
        final_result = result.get("final_result")

        if not success:
            error = result.get("error", "未知错误")
            return f"【子查询 {index}】{subq}\nSQL: {sql or '(无)'}\n状态: 失败（{error}）"

        # 解析结果集结构
        row_count = 0
        columns: List[str] = []
        sample_rows: List[Any] = []

        if isinstance(final_result, list):
            row_count = len(final_result)
            if row_count > 0:
                first = final_result[0]
                # 字典行（{col: val}）或元组/列表行
                if hasattr(first, "keys"):
                    columns = list(first.keys())
                elif isinstance(first, (list, tuple)):
                    columns = [f"col_{i}" for i in range(len(first))]
                sample_rows = final_result[:SAMPLE_ROWS]
        elif final_result is not None:
            # 标量结果
            row_count = 1
            columns = ["value"]
            sample_rows = [final_result]

        lines = [
            f"【子查询 {index}】{subq}",
            f"SQL: {sql}",
            f"结果行数: {row_count}",
        ]
        if columns:
            lines.append(f"列名: {', '.join(str(c) for c in columns)}")
        if sample_rows:
            lines.append(f"前 {len(sample_rows)} 行样本:")
            for row in sample_rows:
                if hasattr(row, "keys"):
                    row_str = ", ".join(f"{k}={v}" for k, v in row.items())
                else:
                    row_str = str(row)
                lines.append(f"  - {row_str}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 降级拼接
    # ------------------------------------------------------------------
    @staticmethod
    def _fallback(subquery_results: List[Dict[str, Any]]) -> str:
        """LLM 不可用时简单拼接各子查询结果。"""
        parts = []
        for i, r in enumerate(subquery_results, 1):
            if r.get("success"):
                parts.append(f"子查询{i}：{r.get('subquery', '')} → {r.get('final_sql', '')}")
            else:
                parts.append(f"子查询{i}：{r.get('subquery', '')} → 失败（{r.get('error', '')}）")
        return "\n".join(parts)

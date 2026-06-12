# ============================================================================
# 可回答性检查模块（决策 23）
# ============================================================================
# 位置：SS 之后、CG 之前
# 原则：宽松——只要有合理可能性就放行，只有明确缺失/粒度严重不匹配才拦截
# 输入：用户查询 + MSchema + IR 元数据
# 输出：answerable(true/false/uncertain) + confidence + reason + 缺失信息
# ============================================================================


import json
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

from loguru import logger


# ---------------------------------------------------------------------------
# Prompt 模板
# ---------------------------------------------------------------------------

# Prompt 已迁移至 src/verification/prompts.py
from src.verification.prompts import ANSWERABILITY_CHECK_PROMPT
from utils.llm_client import parse_json, stream_with_sse


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class AnswerabilityResult:
    """可回答性检查结果"""
    answerable: str = "uncertain"       # "true" | "false" | "uncertain"
    confidence: float = 0.0
    reason: str = ""
    missing_info: str = ""
    granularity_match: str = ""

    @property
    def should_reject(self) -> bool:
        """是否应该拒答（仅 false 拦截）"""
        return self.answerable == "false"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "answerable": self.answerable,
            "confidence": self.confidence,
            "reason": self.reason,
            "missing_info": self.missing_info,
            "granularity_match": self.granularity_match,
        }


# ---------------------------------------------------------------------------
# 检查器
# ---------------------------------------------------------------------------

class AnswerabilityChecker:
    """
    可回答性检查器（决策 23）

    宽松原则：宁可放过，不误杀。只有明确无法回答时才拦截。

    Attributes:
        llm_client: LLM 客户端（用于调用 LLM 做判断）
        strictness: 严格程度，"loose"（默认）或 "strict"
            - loose: 只有 answerable="false" 才拦截
            - strict: answerable="false" 或 confidence < 0.5 的 "uncertain" 也拦截
    """

    def __init__(self, llm_client=None, strictness: str = "loose"):
        self.llm_client = llm_client
        self.strictness = strictness

    def check(
        self,
        user_query: str,
        mschema: List[Any],
        ir_context: Any = None,
    ) -> AnswerabilityResult:
        """
        执行可回答性检查

        Args:
            user_query: 用户原始查询
            mschema: SS 输出的 MSchema 表列表（List[MSchemaTable]）
            ir_context: IR 输出的 RetrievedContext（可选，用于辅助判断）

        Returns:
            AnswerabilityResult: 检查结果
        """
        if not self.llm_client:
            logger.warning("LLM 未设置，可回答性检查跳过（默认放行）")
            return AnswerabilityResult(
                answerable="uncertain",
                confidence=0.5,
                reason="LLM 未设置，跳过检查",
            )

        # 1. 构造 schema 文本
        schema_text = self._format_mschema(mschema)
        if not schema_text.strip():
            return AnswerabilityResult(
                answerable="false",
                confidence=0.9,
                reason="Schema 为空，无法回答任何问题",
                missing_info="没有可用的表和列",
            )

        # 2. 提取 IR 辅助信息
        keywords = []
        lsh_hit_count = 0
        vector_top_scores = []

        if ir_context is not None:
            keywords = getattr(ir_context, "keywords", []) or []
            lsh_hit_count = getattr(ir_context, "lsh_hit_count", 0) or 0
            vector_top_scores = getattr(ir_context, "vector_top_scores", []) or []

        # 3. 调用 LLM
        messages = ANSWERABILITY_CHECK_PROMPT.format_messages(
            user_query=user_query,
            schema_text=schema_text,
            keywords=", ".join(keywords) if keywords else "无",
            lsh_hit_count=lsh_hit_count,
            vector_top_scores=", ".join(f"{s:.3f}" for s in vector_top_scores[:5])
                               if vector_top_scores else "无",
        )

        try:
            raw = stream_with_sse(self.llm_client.stream(messages, as_json=True, temperature=0.0, thinking=False))
            result = parse_json(raw)

            answerable = result.get("answerable", "uncertain").lower()
            if answerable not in ("true", "false", "uncertain"):
                answerable = "uncertain"

            check_result = AnswerabilityResult(
                answerable=answerable,
                confidence=float(result.get("confidence", 0.5)),
                reason=result.get("reason", ""),
                missing_info=result.get("missing_info", ""),
                granularity_match=result.get("granularity_match", ""),
            )

            logger.info(
                f"可回答性检查: answerable={check_result.answerable}, "
                f"confidence={check_result.confidence:.2f}, "
                f"reason={check_result.reason[:100]}"
            )
            return check_result

        except Exception as e:
            logger.error(f"可回答性检查 LLM 调用失败: {e}")
            # LLM 调用失败时默认放行（宽松原则）
            return AnswerabilityResult(
                answerable="uncertain",
                confidence=0.3,
                reason=f"检查调用失败: {e}",
            )

    def should_proceed(self, result: AnswerabilityResult) -> bool:
        """
        根据检查结果和严格程度决定是否继续

        Args:
            result: 检查结果

        Returns:
            bool: True 表示继续，False 表示拒答
        """
        if result.answerable == "false":
            return False
        if self.strictness == "strict" and result.answerable == "uncertain" and result.confidence < 0.5:
            return False
        return True

    @staticmethod
    def _format_mschema(mschema: List[Any]) -> str:
        """将 MSchema 表列表格式化为文本"""
        try:
            from src.schema_selection.schema_selector import MSchemaFormat
            schema_dict = MSchemaFormat.create_mschema_schema(mschema)
            return MSchemaFormat.format_for_llm(schema_dict)
        except Exception:
            # 回退：简单拼接
            lines = []
            for tbl in mschema:
                tbl_name = getattr(tbl, "name", "?")
                lines.append(f"# 表: {tbl_name}")
                for col in getattr(tbl, "columns", []):
                    col_name = getattr(col, "name", "?")
                    col_type = getattr(col, "data_type", "?")
                    col_desc = getattr(col, "description", "")
                    desc_str = f" — {col_desc}" if col_desc else ""
                    lines.append(f"  - {col_name} ({col_type}){desc_str}")
            return "\n".join(lines)

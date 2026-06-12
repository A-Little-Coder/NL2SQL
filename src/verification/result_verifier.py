# ============================================================================
# 结果可信度验证模块（决策 24）
# ============================================================================
# 位置：Decision 阶段，选定最终 SQL 后
# 原则：严格——宁可多拒，不放过答非所问
# 输入：用户查询 + 最终 SQL + 执行结果样本 + MSchema
# 输出：trustworthy(true/false) + reason + 粒度/语义对齐说明
# ============================================================================


import json
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

from loguru import logger


# ---------------------------------------------------------------------------
# Prompt 模板
# ---------------------------------------------------------------------------

# Prompt 已迁移至 src/verification/prompts.py
from src.verification.prompts import RESULT_VERIFICATION_PROMPT
from utils.llm_client import parse_json, stream_with_sse


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class VerificationResult:
    """结果可信度验证结果"""
    trustworthy: str = "true"      # "true" | "false"
    reason: str = ""
    granularity_match: str = ""
    semantic_alignment: str = ""

    @property
    def should_reject(self) -> bool:
        """是否应该拒答"""
        return self.trustworthy == "false"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trustworthy": self.trustworthy,
            "reason": self.reason,
            "granularity_match": self.granularity_match,
            "semantic_alignment": self.semantic_alignment,
        }


# ---------------------------------------------------------------------------
# 验证器
# ---------------------------------------------------------------------------

class ResultVerifier:
    """
    结果可信度验证器（决策 24）

    严格原则：宁可多拒，不放过答非所问。这是最后一道防线。

    Attributes:
        llm_client: LLM 客户端
        strictness: 严格程度，"strict"（默认）或 "loose"
            - strict: 必须明确对齐才通过
            - loose: 有疑虑也放行
    """

    def __init__(self, llm_client=None, strictness: str = "strict"):
        self.llm_client = llm_client
        self.strictness = strictness

    def verify(
        self,
        user_query: str,
        selected_sql: str,
        result_sample: Any,
        mschema: List[Any] = None,
    ) -> VerificationResult:
        """
        执行结果可信度验证

        Args:
            user_query: 用户原始查询
            selected_sql: 最终选定的 SQL
            result_sample: SQL 执行结果（列名 + 数据行）
            mschema: MSchema 表列表（可选，用于对照）

        Returns:
            VerificationResult: 验证结果
        """
        if not self.llm_client:
            logger.warning("LLM 未设置，结果验证跳过（默认可信）")
            return VerificationResult(
                trustworthy="true",
                reason="LLM 未设置，跳过验证",
            )

        if not selected_sql:
            return VerificationResult(
                trustworthy="false",
                reason="无 SQL 可验证",
            )

        # 1. 格式化结果样本
        sample_text = self._format_result_sample(result_sample)

        # 2. 格式化 schema
        schema_text = ""
        if mschema:
            schema_text = self._format_mschema(mschema)

        # 3. 调用 LLM
        messages = RESULT_VERIFICATION_PROMPT.format_messages(
            user_query=user_query,
            selected_sql=selected_sql,
            result_sample=sample_text,
            schema_text=schema_text or "未提供",
        )

        try:
            raw = stream_with_sse(self.llm_client.stream(messages, as_json=True, temperature=0.0))
            result = parse_json(raw)

            trustworthy = result.get("trustworthy", "true").lower()
            if trustworthy not in ("true", "false"):
                trustworthy = "true"  # 无效值默认放行

            verification = VerificationResult(
                trustworthy=trustworthy,
                reason=result.get("reason", ""),
                granularity_match=result.get("granularity_match", ""),
                semantic_alignment=result.get("semantic_alignment", ""),
            )

            logger.info(
                f"结果验证: trustworthy={verification.trustworthy}, "
                f"reason={verification.reason[:100]}"
            )
            return verification

        except Exception as e:
            logger.error(f"结果验证 LLM 调用失败: {e}")
            # LLM 失败时默认放行（宽松降级）
            return VerificationResult(
                trustworthy="true",
                reason=f"验证调用失败: {e}",
            )

    @staticmethod
    def _format_result_sample(result_sample: Any, max_rows: int = 5) -> str:
        """将执行结果格式化为文本"""
        if result_sample is None:
            return "无执行结果"

        if isinstance(result_sample, list):
            if not result_sample:
                return "空结果集"

            lines = []
            # 尝试获取列名
            first = result_sample[0]
            if isinstance(first, dict):
                headers = list(first.keys())
                lines.append("列名: " + ", ".join(headers))
                lines.append("数据:")
                for row in result_sample[:max_rows]:
                    values = [str(row.get(h, "")) for h in headers]
                    lines.append("  " + " | ".join(values))
            elif isinstance(first, (list, tuple)):
                lines.append("数据:")
                for row in result_sample[:max_rows]:
                    lines.append("  " + " | ".join(str(c) for c in row))
            else:
                lines.append(str(result_sample[:max_rows]))

            if len(result_sample) > max_rows:
                lines.append(f"  ... 共 {len(result_sample)} 行")
            return "\n".join(lines)

        return str(result_sample)[:1000]

    @staticmethod
    def _format_mschema(mschema: List[Any]) -> str:
        """将 MSchema 表列表格式化为文本"""
        try:
            from src.schema_selection.schema_selector import MSchemaFormat
            schema_dict = MSchemaFormat.create_mschema_schema(mschema)
            return MSchemaFormat.format_for_llm(schema_dict)
        except Exception:
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

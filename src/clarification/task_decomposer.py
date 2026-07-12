# ============================================================================
# TaskDecomposer：意图理解 + 多意图分解（v2 精简版）
# ============================================================================
# 位置：Rewrite 子图之后、IR 之前
# 职责：
#   1. 判断用户查询是单意图还是多意图
#   2. 多意图分解：把复合查询拆为独立子查询
# 输入：已是 Rewrite 改写后的完整语义 query
# 输出：PlanResult（execute 单意图/多意图子查询列表）
# 不再有 CLARIFY/REJECT 功能（已移入 Rewrite 子图）
# ============================================================================

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from loguru import logger

from src.clarification.prompts import TASK_DECOMPOSER_PROMPT
from utils.llm_client import parse_json, stream_with_sse


Verdict = Literal["execute"]
IntentType = Literal["single", "multi"]


@dataclass
class PlanResult:
    """TaskDecomposer 裁决结果

    Attributes:
        verdict: 裁决类型，始终为 execute
        intent_type: single / multi
        subqueries: 分解后的子查询列表（single 时长度为 1）
        reason: 裁决理由（简短）
    """

    verdict: Verdict = "execute"
    intent_type: IntentType = "single"
    subqueries: List[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "intent_type": self.intent_type,
            "subqueries": self.subqueries,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PlanResult":
        """从 LLM 返回的 dict 构造，做字段校验与降级。"""
        verdict = str(d.get("verdict", "execute")).lower()
        if verdict != "execute":
            verdict = "execute"  # 非 execute 降级为执行

        intent_type = str(d.get("intent_type", "single")).lower()
        if intent_type not in ("single", "multi"):
            intent_type = "single"

        subqueries = d.get("subqueries") or []
        if not isinstance(subqueries, list):
            subqueries = []
        subqueries = [str(s).strip() for s in subqueries if str(s).strip()]

        # verdict=execute 但 subqueries 为空 → 由 plan() 用原始 query 填充
        if not subqueries:
            subqueries = []

        return cls(
            verdict=verdict,
            intent_type=intent_type,
            subqueries=subqueries,
            reason=str(d.get("reason", "") or ""),
        )


class TaskDecomposer:
    """
    意图理解与任务分解节点（v2：精简版，无反问/拒答功能）

    Attributes:
        llm_client: LLM 客户端
    """

    def __init__(self, llm_client=None):
        self.llm_client = llm_client

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------
    def plan(
        self,
        user_query: str,
        db_id: Optional[str] = None,
    ) -> PlanResult:
        """
        对用户查询做意图拆解（单意图/多意图分解）

        Args:
            user_query: 用户查询（已是 Rewrite 改写后的完整语义 query）
            db_id: 当前数据库 id（可选，用于粗筛数据范围）

        Returns:
            PlanResult
        """
        # 空查询兜底
        if not user_query or not user_query.strip():
            return PlanResult(
                verdict="execute",
                intent_type="single",
                subqueries=[],
                reason="空查询",
            )

        # LLM 未设置 → 降级为单意图执行
        if not self.llm_client:
            logger.warning("TaskDecomposer: LLM 未设置，降级为单意图执行")
            return PlanResult(
                verdict="execute",
                intent_type="single",
                subqueries=[user_query],
                reason="LLM 未设置，降级执行",
            )

        # 调 LLM 做意图拆解
        try:
            messages = TASK_DECOMPOSER_PROMPT.format_messages(
                user_query=user_query,
                db_id=db_id or "（未指定）",
            )
            raw = stream_with_sse(
                self.llm_client.stream(
                    messages,
                    as_json=True,
                    temperature=0.0,
                    thinking=False,
                    run_name="task-decomposer",
                )
            )
            result_dict = parse_json(raw)
            plan = PlanResult.from_dict(result_dict)
        except Exception as e:
            logger.error(f"TaskDecomposer LLM 调用失败，降级为单意图执行: {e}")
            return PlanResult(
                verdict="execute",
                intent_type="single",
                subqueries=[user_query],
                reason=f"LLM 调用失败，降级执行: {e}",
            )

        # execute 兜底：subqueries 为空时用原始 query
        if not plan.subqueries:
            plan.subqueries = [user_query]
            plan.intent_type = "single"

        # single 意图强制 subqueries 长度为 1
        if plan.intent_type == "single":
            plan.subqueries = [plan.subqueries[0] if plan.subqueries else user_query]

        logger.info(
            f"TaskDecomposer: verdict={plan.verdict}, intent={plan.intent_type}, "
            f"subqueries={len(plan.subqueries)}, reason={plan.reason[:80]}"
        )
        return plan
# ============================================================================
# TaskPlanner：意图理解 + 三选一裁决（决策 9 / 10）
# ============================================================================
# 位置：history_cache 之后、ir 之前（反问前移到 IR 之前）
# 职责：
#   1. 判断用户问句是否清晰：清晰→EXECUTE，歧义→CLARIFY，不可答→REJECT
#   2. 多意图分解：把复合查询拆为独立子查询（保持语义完整）
#   3. 歧义识别：标注歧义实体 + 候选解释，生成澄清问题（粗/细粒度自适应）
#   4. 拒答判定：越权写操作 / 超出数据范围 / 无法理解
#
# 输入：user_query + conversation_history（可选）+ db_id（可选）+ clarified（可选，反问后的回答）
# 输出：PlanResult（结构化）
#
# 与 answerability_check 的关系（决策 9）：
#   - task_planner（IR 前）拦截"问得不清楚"（意图层）
#   - answerability_check（SS 后）拦截"答得不对题"（数据维度层）
#   - 二者并存分层，不互相替代
# ============================================================================

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from loguru import logger

from src.clarification.prompts import TASK_PLANNER_PROMPT
from utils.llm_client import parse_json, stream_with_sse


# ---------------------------------------------------------------------------
# 写操作关键词（REJECT 判定用，决策 9）
# ---------------------------------------------------------------------------
# 先于 LLM 做硬性检测，避免写操作意图浪费 LLM 调用
WRITE_OPERATION_KEYWORDS = [
    "insert", "update", "delete", "drop", "create", "alter", "truncate",
    "replace", "merge", "grant", "revoke",
]
# 中文写操作意图词
WRITE_OPERATION_ZH = [
    "删除", "修改", "更新", "插入", "清空", "清空表", "建表", "删除表",
    "添加数据", "改数据", "删数据",
]


Verdict = Literal["execute", "clarify", "reject"]
IntentType = Literal["single", "multi"]


@dataclass
class PlanResult:
    """TaskPlanner 裁决结果

    Attributes:
        verdict: 裁决类型 execute / clarify / reject
        intent_type: execute 时填 single / multi
        subqueries: execute 时分解后的子查询列表（single 时长度为 1）
        ambiguities: clarify 时标注的歧义 [{entity, candidates}]
        clarify_question: clarify 时要问用户的问题
        reject_reason: reject 时的拒答原因
        reason: 裁决理由（简短）
    """

    verdict: Verdict = "execute"
    intent_type: IntentType = "single"
    subqueries: List[str] = field(default_factory=list)
    ambiguities: List[Dict[str, Any]] = field(default_factory=list)
    clarify_question: str = ""
    reject_reason: str = ""
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "intent_type": self.intent_type,
            "subqueries": self.subqueries,
            "ambiguities": self.ambiguities,
            "clarify_question": self.clarify_question,
            "reject_reason": self.reject_reason,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PlanResult":
        """从 LLM 返回的 dict 构造，做字段校验与降级。"""
        verdict = str(d.get("verdict", "execute")).lower()
        if verdict not in ("execute", "clarify", "reject"):
            verdict = "execute"  # 未知 verdict 降级为执行

        intent_type = str(d.get("intent_type", "single")).lower()
        if intent_type not in ("single", "multi"):
            intent_type = "single"

        subqueries = d.get("subqueries") or []
        if not isinstance(subqueries, list):
            subqueries = []
        subqueries = [str(s).strip() for s in subqueries if str(s).strip()]

        ambiguities = d.get("ambiguities") or []
        if not isinstance(ambiguities, list):
            ambiguities = []

        # verdict=execute 但 subqueries 为空 → 用原始 query 兜底（单意图）
        if verdict == "execute" and not subqueries:
            subqueries = []  # 由 plan() 用原始 query 填充

        return cls(
            verdict=verdict,
            intent_type=intent_type,
            subqueries=subqueries,
            ambiguities=ambiguities,
            clarify_question=str(d.get("clarify_question", "") or ""),
            reject_reason=str(d.get("reject_reason", "") or ""),
            reason=str(d.get("reason", "") or ""),
        )


class TaskPlanner:
    """
    意图理解与任务规划节点（反问机制的入口，决策 9/10）

    Attributes:
        llm_client: LLM 客户端
        max_clarify_rounds: 反问上限（默认 5，决策 13）
    """

    def __init__(self, llm_client=None, max_clarify_rounds: int = 5):
        self.llm_client = llm_client
        self.max_clarify_rounds = max_clarify_rounds

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------
    def plan(
        self,
        user_query: str,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        db_id: Optional[str] = None,
        clarified: Optional[str] = None,
    ) -> PlanResult:
        """
        对用户查询做三选一裁决

        Args:
            user_query: 用户原始查询
            conversation_history: 会话历史（可选，辅助 follow-up 理解）
            db_id: 当前数据库 id（可选，用于粗筛数据范围）
            clarified: 反问后用户的回答（可选，resume 时传入，作为澄清上下文）

        Returns:
            PlanResult
        """
        if not user_query or not user_query.strip():
            return PlanResult(
                verdict="reject",
                reject_reason="查询为空，无法理解意图",
                reason="空查询",
            )

        # 1. 硬性写操作检测（先于 LLM，避免浪费调用）
        if self._detect_write_operation(user_query):
            return PlanResult(
                verdict="reject",
                reject_reason="本服务仅支持查询，不支持数据写操作（删除/修改/插入等）",
                reason="检测到写操作意图",
            )

        # 2. LLM 未设置 → 降级为单意图执行（不阻塞主流程）
        if not self.llm_client:
            logger.warning("TaskPlanner: LLM 未设置，降级为单意图执行")
            return PlanResult(
                verdict="execute",
                intent_type="single",
                subqueries=[user_query],
                reason="LLM 未设置，降级执行",
            )

        # 3. 调 LLM 做三选一裁决
        try:
            clarified_context = self._format_clarified_context(clarified, conversation_history)
            messages = TASK_PLANNER_PROMPT.format_messages(
                user_query=user_query,
                db_id=db_id or "（未指定）",
                clarified_context=clarified_context,
            )
            raw = stream_with_sse(
                self.llm_client.stream(
                    messages,
                    as_json=True,
                    temperature=0.0,
                    thinking=False,
                    run_name="task-planner",
                )
            )
            result_dict = parse_json(raw)
            plan = PlanResult.from_dict(result_dict)
        except Exception as e:
            logger.error(f"TaskPlanner LLM 调用失败，降级为单意图执行: {e}")
            return PlanResult(
                verdict="execute",
                intent_type="single",
                subqueries=[user_query],
                reason=f"LLM 调用失败，降级执行: {e}",
            )

        # 4. execute 兜底：subqueries 为空时用原始 query
        if plan.verdict == "execute" and not plan.subqueries:
            plan.subqueries = [user_query]
            plan.intent_type = "single"

        # 5. single 意图强制 subqueries 长度为 1（取第一个或原始 query）
        if plan.verdict == "execute" and plan.intent_type == "single":
            plan.subqueries = [plan.subqueries[0] if plan.subqueries else user_query]

        logger.info(
            f"TaskPlanner: verdict={plan.verdict}, intent={plan.intent_type}, "
            f"subqueries={len(plan.subqueries)}, reason={plan.reason[:80]}"
        )
        return plan

    # ------------------------------------------------------------------
    # 写操作检测（决策 9：REJECT 越权写操作）
    # ------------------------------------------------------------------
    @staticmethod
    def _detect_write_operation(query: str) -> bool:
        """检测查询是否包含写操作意图（SQL 关键词 + 中文写操作词）。

        先于 LLM 做硬性检测，命中直接 REJECT，避免浪费 LLM 调用。
        """
        q_lower = query.lower()
        # SQL 写操作关键词（带词边界，避免误匹配如 "updated_at" 列名）
        for kw in WRITE_OPERATION_KEYWORDS:
            if re.search(r"\b" + re.escape(kw) + r"\b", q_lower):
                return True
        # 中文写操作意图词
        for kw in WRITE_OPERATION_ZH:
            if kw in query:
                return True
        return False

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------
    @staticmethod
    def _format_clarified_context(
        clarified: Optional[str],
        conversation_history: Optional[List[Dict[str, Any]]],
    ) -> str:
        """格式化已有澄清上下文（反问 resume 时传入用户回答）。"""
        parts = []
        if clarified:
            parts.append(f"用户上一轮澄清回答：{clarified}")
        if conversation_history:
            # 取最近 3 轮，避免上下文过长
            recent = conversation_history[-3:]
            for turn in recent:
                role = turn.get("role", "user")
                content = str(turn.get("content", ""))[:200]
                parts.append(f"[{role}] {content}")
        return "\n".join(parts) if parts else "（无）"

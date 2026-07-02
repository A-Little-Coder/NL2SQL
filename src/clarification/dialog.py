# ============================================================================
# DialogManager：反问中断与恢复（决策 12 / 13）
# ============================================================================
# 职责：
#   1. 包装 LangGraph interrupt()：把反问问题抛给调用方（前端），暂停图
#   2. resume 时返回用户回答
#   3. 拒答关键词识别（"不知道/跳过/算了/skip/不清楚/随便"）→ 立即退出循环
#   4. 5 次硬上限：达到上限不再 interrupt，返回拒答信号
#
# 设计要点（决策 13）：
#   - 拒答关键词列表可配置（config/clarification.yaml 的 decline_keywords）
#   - 硬上限检查在 interrupt() 之前，达上限直接返回拒答信号
#   - 计数器由调用方（task_planner 节点）存 state，本类只负责判断与 interrupt
# ============================================================================

from typing import Any, Dict, List, Optional

from loguru import logger

try:
    from langgraph.types import interrupt
except Exception:  # pragma: no cover - 无 langgraph 时（不应发生）
    interrupt = None  # type: ignore


# 默认拒答关键词（决策 13，可被 config 覆盖）
DEFAULT_DECLINE_KEYWORDS = ["不知道", "跳过", "算了", "skip", "不清楚", "随便"]

# 默认反问上限（决策 13）
DEFAULT_MAX_ROUNDS = 5

# 拒答信号：用户拒答或达上限时，ask() 返回此值
DECLINED = "__DECLINED__"
MAX_REACHED = "__MAX_ROUNDS_REACHED__"


class DialogManager:
    """
    反问对话管理器（决策 12/13）

    包装 LangGraph interrupt()，提供拒答识别与硬上限判断。

    Attributes:
        max_rounds: 反问上限（默认 5）
        decline_keywords: 拒答关键词列表
    """

    def __init__(
        self,
        max_rounds: int = DEFAULT_MAX_ROUNDS,
        decline_keywords: Optional[List[str]] = None,
    ):
        self.max_rounds = max_rounds
        self.decline_keywords = list(decline_keywords) if decline_keywords else list(DEFAULT_DECLINE_KEYWORDS)

    # ------------------------------------------------------------------
    # 公开接口：反问（包装 interrupt）
    # ------------------------------------------------------------------
    def ask(
        self,
        clarify_question: str,
        clarify_round: int,
        ambiguities: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """
        向用户发起一次反问（首次执行挂起，resume 返回用户回答）

        Args:
            clarify_question: 要问用户的问题
            clarify_round: 当前已反问轮次（由 state 提供）
            ambiguities: 歧义候选（可选，随反问上下文一起抛给前端）

        Returns:
            用户回答字符串；若达上限返回 MAX_REACHED；用户回答被识别为拒答返回 DECLINED
            （调用方据此退出循环或用最佳猜测继续）

        Note:
            interrupt() 的 value 是抛给调用方/前端的结构化上下文。
            首次执行：抛 GraphInterrupt，图挂起；resume：返回 Command(resume=...) 的值。
        """
        # 1. 硬上限检查：达上限不再 interrupt，返回信号
        if clarify_round >= self.max_rounds:
            logger.warning(f"反问达到 {self.max_rounds} 次上限，强制退出循环")
            return MAX_REACHED

        if interrupt is None:  # pragma: no cover
            raise RuntimeError("langgraph 未安装，无法使用 interrupt")

        # 2. 构造反问上下文（抛给前端的结构化数据）
        clarify_context = {
            "question": clarify_question,
            "ambiguities": ambiguities or [],
            "round": clarify_round + 1,
            "max_rounds": self.max_rounds,
        }

        # 3. interrupt：首次挂起，resume 返回用户回答
        user_answer = interrupt(clarify_context)

        # ↓ resume 后执行 ↓
        if not isinstance(user_answer, str):
            user_answer = str(user_answer) if user_answer is not None else ""

        # 4. 拒答识别
        if self.is_decline(user_answer):
            logger.info(f"用户拒答（回答={user_answer!r}），退出反问循环")
            return DECLINED

        return user_answer

    # ------------------------------------------------------------------
    # 拒答识别（决策 13）
    # ------------------------------------------------------------------
    def is_decline(self, answer: str) -> bool:
        """识别用户回答是否为拒答关键词。

        匹配规则：回答（小写化、去空白）包含任一拒答关键词即为拒答。
        """
        if not answer:
            return False
        a = answer.strip().lower()
        return any(kw.lower() in a for kw in self.decline_keywords)

    # ------------------------------------------------------------------
    # 辅助判断
    # ------------------------------------------------------------------
    def reached_max(self, clarify_round: int) -> bool:
        """是否已达到反问上限（供调用方在 interrupt 前预判）。"""
        return clarify_round >= self.max_rounds

    @staticmethod
    def is_declined_signal(answer: str) -> bool:
        """ask() 返回值是否为拒答信号（DECLINED 或 MAX_REACHED）。"""
        return answer in (DECLINED, MAX_REACHED)

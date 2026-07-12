# ============================================================================
# 前置拒答检测节点（PreReject）- v2 设计 + LLM 语义判定升级（D9）
# ============================================================================
# 功能：
#   1. 空查询/纯空白 -> 规则快路径直接拒答（不调 LLM）
#   2. 其余查询调 LLM 语义判定：增删改意图(write_op) / 危险信息(dangerous_info) / 正常(normal)
#      - reject=true -> 拒答，设 rejection_reason + rewrite_rejection_reason + pre_reject_category
#      - reject=false -> 放行，pre_reject_category=normal
#   3. LLM 异常/不可用 -> 降级放行（pre_reject_category=normal，宁放行不误杀）
# ============================================================================

import logging
from typing import Any, Dict

from src.graph.state import NL2SQLState
from src.rewrite.prompts import PRE_REJECT_PROMPT
from utils.llm_client import parse_json, stream_with_sse

logger = logging.getLogger(__name__)


# 友好拒答原因模板（按类别）
_REJECT_REASON_TEMPLATES = {
    "write_op": "本服务仅支持查询，不支持数据写操作（删除/修改/插入/建表等）",
    "dangerous_info": "该查询涉及危险信息指令（系统表刺探/敏感数据导出等），已拦截",
}


def _normalize_category(category: str) -> str:
    """规范化 LLM 返回的 category，非法值回退 normal。"""
    if category in ("write_op", "dangerous_info", "normal"):
        return category
    return "normal"


def make_pre_reject_node(llm_client: Any = None) -> callable:
    """
    构造前置拒答检测节点。

    空查询走规则快路径；其余调 LLM 语义判定（thinking=False, run_name=pre-reject）。
    LLM 异常/不可用降级放行。

    Args:
        llm_client: LLM 客户端实例（None 时除空查询外均放行）

    Returns:
        Callable[[NL2SQLState], Dict[str, Any]]: LangGraph 节点函数
    """

    def pre_reject_node(state: NL2SQLState) -> Dict[str, Any]:
        """
        前置拒答检测主逻辑。

        输入（从 state 读取）:
            - user_query: 当前用户查询

        输出（返回 dict，LangGraph 浅合并）:
            - rejection_reason: 主流程拒答原因（违规时设）
            - rewrite_rejection_reason: 前置拒答原因（违规时设，触发 route END）
            - pre_reject_category: 判定类别（write_op/dangerous_info/normal）
            - trace_log: 追加的轨迹日志
        """
        user_query = state.get("user_query", "")
        trace_log = state.get("trace_log", [])[:]

        # 1. 空查询规则快路径（不调 LLM）
        if not user_query or not user_query.strip():
            return {
                "rejection_reason": "查询为空，无法理解意图",
                "rewrite_rejection_reason": "查询为空",
                "pre_reject_category": "normal",
                "trace_log": trace_log + ["[PreReject] 空查询拒答"],
            }

        # 2. LLM 语义判定
        category = "normal"
        reject_reason: str = ""
        try:
            if llm_client:
                messages = PRE_REJECT_PROMPT.format_messages(user_query=user_query)
                raw = stream_with_sse(
                    llm_client.stream(messages, as_json=True, temperature=0.0,
                                      thinking=False, run_name="pre-reject")
                )
                result = parse_json(raw) or {}
                category = _normalize_category(str(result.get("category", "normal")).strip())
                reject = bool(result.get("reject", False))
                llm_reason = str(result.get("reason", "")).strip()
                if reject and category in ("write_op", "dangerous_info"):
                    base = _REJECT_REASON_TEMPLATES.get(category, "")
                    reject_reason = f"{base}（{llm_reason}）" if llm_reason else base
            else:
                logger.info("[PreReject] LLM 不可用，降级放行")
        except Exception as e:
            logger.warning(f"[PreReject] LLM 判定异常，降级放行: {e}")
            category = "normal"

        # 3. 拒答 / 放行
        if reject_reason:
            return {
                "rejection_reason": reject_reason,
                "rewrite_rejection_reason": f"前置拒答: {category}",
                "pre_reject_category": category,
                "trace_log": trace_log + [f"[PreReject] 拒答 category={category}"],
            }

        return {
            "pre_reject_category": category,
            "trace_log": trace_log + [f"[PreReject] 通过 category={category}"],
        }

    return pre_reject_node

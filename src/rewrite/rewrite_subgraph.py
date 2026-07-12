# ============================================================================
# Rewrite 子图（v2 设计）
# ============================================================================
# 结构：三个子节点通过条件边循环协作
#   1. detect_issues — 检测指代/歧义/对象缺失
#   2. rewrite_execute — 利用上下文改写 query
#   3. clarify — 反问澄清（interrupt），等待用户补充信息
# 循环逻辑：
#   - 改写最多 2 次（条件边循环，不经过反问）
#   - 2 次后仍有问题 → 触发反问澄清（interrupt）
#   - 用户补充信息 → 继续改写循环（可多次反问，直到检测通过）
# ============================================================================

from typing import Any, Callable, Dict, List, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from loguru import logger

from src.rewrite.prompts import DETECT_ISSUES_PROMPT, REWRITE_EXECUTE_PROMPT
from utils.llm_client import parse_json, stream_with_sse
from src.api.streaming import emit_safe

# LangGraph interrupt
try:
    from langgraph.types import interrupt
except ImportError:
    interrupt = None


# ---------------------------------------------------------------------------
# 子图 State
# ---------------------------------------------------------------------------
class RewriteSubgraphState(TypedDict, total=False):
    """Rewrite 子图内部状态"""
    user_query: str                     # 当前查询（可能被改写）
    original_query: str                 # 原始查询（不变）
    conversation_history: List[Dict]    # 前 5 轮会话历史
    rewrite_round: int                  # 已改写次数（0/1/2）
    rewrite_reason: str                 # 改写说明
    clarify_context: str                # 用户补充信息（反问回答）
    clarify_round: int                  # 已反问次数
    has_issues: bool                    # 检测结果
    issue_detail: str                   # 问题描述
    issue_types: List[str]              # 问题类型列表
    final_verdict: str                  # 最终输出：pass/reject
    rejection_reason: str               # 拒答原因


# ---------------------------------------------------------------------------
# 历史格式化
# ---------------------------------------------------------------------------
def _format_history_lines(history: Optional[List[Dict[str, Any]]]) -> str:
    """格式化前 5 轮会话历史为文本供 Prompt 使用。"""
    if not history:
        return "（无）"
    recent = history[-5:]
    lines = []
    for i, turn in enumerate(recent, 1):
        content = str(turn.get("user_query") or turn.get("content", ""))[:300]
        if not content:
            continue
        rejection = turn.get("rejection_reason") or turn.get("rewrite_rejection_reason")
        extra = f" [被拒答: {rejection[:60]}]" if rejection else ""
        lines.append(f"{i}. [user] {content}{extra}")
    return "\n".join(lines) if lines else "（无）"


# ---------------------------------------------------------------------------
# 子节点 1：问题检测
# ---------------------------------------------------------------------------
def make_detect_issues_node(llm_client: Any) -> Callable:
    """构造问题检测子节点：检测指代/歧义/对象缺失。

    Returns:
        节点函数，输出 has_issues/issue_detail/issue_types
    """

    def node(state: RewriteSubgraphState) -> Dict[str, Any]:
        user_query = state.get("user_query", "")
        history_lines = _format_history_lines(state.get("conversation_history", []))

        if not llm_client:
            # 无 LLM 时视为无问题
            return {"has_issues": False, "issue_detail": "", "issue_types": []}

        try:
            messages = DETECT_ISSUES_PROMPT.format_messages(
                user_query=user_query,
                history_lines=history_lines,
            )
            raw = stream_with_sse(
                llm_client.stream(messages, as_json=True, temperature=0.0,
                                  thinking=False, run_name="detect-issues")
            )
            result = parse_json(raw)
            has_issues = bool(result.get("has_issues", False))
            return {
                "has_issues": has_issues,
                "issue_detail": str(result.get("issue_detail", "")),
                "issue_types": list(result.get("issue_types", [])),
            }
        except Exception as e:
            logger.error(f"DetectIssues LLM 调用失败，降级为无问题: {e}")
            return {"has_issues": False, "issue_detail": "", "issue_types": []}

    return node


# ---------------------------------------------------------------------------
# 子节点 2：改写执行
# ---------------------------------------------------------------------------
def make_rewrite_execute_node(llm_client: Any) -> Callable:
    """构造改写执行子节点：利用上下文改写 query。

    Returns:
        节点函数，输出 rewritten_query/rewrite_reason
    """

    def node(state: RewriteSubgraphState) -> Dict[str, Any]:
        user_query = state.get("user_query", "")
        history_lines = _format_history_lines(state.get("conversation_history", []))
        clarify_context = state.get("clarify_context", "")
        current_round = state.get("rewrite_round", 0) + 1

        if not llm_client:
            # 无 LLM 时透传原 query
            return {"rewritten_query": user_query, "rewrite_reason": "LLM 不可用，透传"}

        try:
            messages = REWRITE_EXECUTE_PROMPT.format_messages(
                user_query=user_query,
                history_lines=history_lines,
                clarify_context=clarify_context or "（无）",
            )
            raw = stream_with_sse(
                llm_client.stream(messages, as_json=True, temperature=0.0,
                                  thinking=False, run_name="rewrite-execute")
            )
            result = parse_json(raw)
            rewritten_query = str(result.get("rewritten_query", user_query)).strip()
            rewrite_reason = str(result.get("rewrite_reason", "")).strip()
            if not rewritten_query:
                rewritten_query = user_query

            # Emit rewrite SSE 事件
            emit_safe("rewrite", {
                "rewritten_query": rewritten_query,
                "rewrite_reason": rewrite_reason,
                "rewrite_round": current_round,
            })

            logger.info(f"Rewrite: round={current_round}, reason={rewrite_reason[:80]}")
            return {
                "rewritten_query": rewritten_query,
                "rewrite_reason": rewrite_reason,
                "rewrite_round": current_round,
            }
        except Exception as e:
            logger.error(f"RewriteExecute LLM 调用失败，降级透传: {e}")
            return {"rewritten_query": user_query, "rewrite_reason": "LLM 异常，透传"}

    return node


# ---------------------------------------------------------------------------
# 子节点 3：反问澄清
# ---------------------------------------------------------------------------
def make_clarify_node() -> Callable:
    """构造反问澄清子节点：interrupt 挂起，等待用户补充信息。

    用户补充的信息放入 clarify_context 字段，供 rewrite_execute 使用。

    Returns:
        节点函数，输出 clarify_context/clarify_round
    """

    def node(state: RewriteSubgraphState) -> Dict[str, Any]:
        user_query = state.get("user_query", "")
        issue_detail = state.get("issue_detail", "")
        current_round = state.get("clarify_round", 0) + 1

        # 构造反问问题
        if issue_detail:
            question = f"您的查询「{user_query}」存在以下问题：{issue_detail}。请补充相关信息。"
        else:
            question = f"您的查询「{user_query}」信息不完整，请补充说明您想查询什么。"

        # interrupt 挂起
        if interrupt is None:
            logger.warning("Clarify: interrupt 不可用，降级放行")
            return {"clarify_context": "", "clarify_round": current_round}

        payload = {
            "question": question,
            "ambiguities": [],
            "round": current_round,
        }
        try:
            user_answer = interrupt(payload)
        except RuntimeError:
            # 不在 graph 上下文中（如测试场景），降级放行
            logger.warning("Clarify: interrupt 不在 graph 上下文中，降级放行")
            return {"clarify_context": "", "clarify_round": current_round}
        answer_str = str(user_answer).strip()

        logger.info(f"Clarify: round={current_round}, answer={answer_str[:80]}")
        return {
            "clarify_context": answer_str,
            "clarify_round": current_round,
        }

    return node


# ---------------------------------------------------------------------------
# 路由函数
# ---------------------------------------------------------------------------
MAX_REWRITE_ROUNDS = 2
MAX_CLARIFY_ROUNDS = 5


def route_after_detect(state: RewriteSubgraphState) -> str:
    """问题检测后路由：
    - 无问题 → 输出到主图
    - 有问题 + 有改写次数 → rewrite_execute
    - 有问题 + 无改写次数 → clarify
    """
    if not state.get("has_issues", False):
        return "output"
    rewrite_round = state.get("rewrite_round", 0)
    if rewrite_round < MAX_REWRITE_ROUNDS:
        return "rewrite_execute"
    return "clarify"


def route_after_rewrite(state: RewriteSubgraphState) -> str:
    """改写后路由：回到 detect_issues 再检测。"""
    return "detect_issues"


def route_after_clarify(state: RewriteSubgraphState) -> str:
    """反问后路由：
    - 用户给了有效回答 → rewrite_execute（用新上下文改写）
    - 空回答或拒答信号 → 降级输出
    """
    clarify_context = state.get("clarify_context", "")
    clarify_round = state.get("clarify_round", 0)

    # 拒答信号
    decline_keywords = ["不知道", "跳过", "算了", "skip", "不清楚", "随便"]
    is_declined = any(kw in clarify_context for kw in decline_keywords) if clarify_context else True

    if is_declined or clarify_round >= MAX_CLARIFY_ROUNDS:
        return "output_degraded"

    return "rewrite_execute"


# ---------------------------------------------------------------------------
# 子图构建
# ---------------------------------------------------------------------------
def build_rewrite_subgraph(llm_client: Any = None) -> Any:
    """构建 Rewrite 子图。

    Args:
        llm_client: LLM 客户端实例（None 时降级放行）

    Returns:
        CompiledGraph: 已编译的 Rewrite 子图
    """
    subgraph = StateGraph(RewriteSubgraphState)

    # 添加节点
    subgraph.add_node("detect_issues", make_detect_issues_node(llm_client))
    subgraph.add_node("rewrite_execute", make_rewrite_execute_node(llm_client))
    subgraph.add_node("clarify", make_clarify_node())

    # START → detect_issues
    subgraph.add_edge(START, "detect_issues")

    # detect_issues 条件路由
    subgraph.add_conditional_edges(
        "detect_issues",
        route_after_detect,
        {
            "output": END,
            "rewrite_execute": "rewrite_execute",
            "clarify": "clarify",
        },
    )

    # rewrite_execute → detect_issues（循环回检测）
    subgraph.add_edge("rewrite_execute", "detect_issues")

    # clarify 条件路由
    subgraph.add_conditional_edges(
        "clarify",
        route_after_clarify,
        {
            "rewrite_execute": "rewrite_execute",
            "output_degraded": END,
        },
    )

    return subgraph.compile().with_config(run_name="rewrite-subgraph")


# ---------------------------------------------------------------------------
# 主图适配器：将 Rewrite 子图包装为与主图 NL2SQLState 对接的节点
# ---------------------------------------------------------------------------
def make_rewrite_node(llm_client: Any = None) -> Callable[[Any], Dict[str, Any]]:
    """构造与主图 NL2SQLState 对接的 Rewrite 节点。

    此节点作为主图的一个节点，内部调用 Rewrite 子图。

    Args:
        llm_client: LLM 客户端实例

    Returns:
        Callable[[NL2SQLState], Dict[str, Any]]: 主图节点函数
    """
    rewrite_subgraph = build_rewrite_subgraph(llm_client)

    def rewrite_node(state: Any) -> Dict[str, Any]:
        """
        Rewrite 节点主逻辑。

        输入（从主图 state 读取）:
            - user_query: 当前用户查询
            - conversation_history: 会话历史列表

        输出（返回 dict，LangGraph 浅合并）:
            - user_query: 可能被改写后的查询
            - rewritten_query: 最终改写结果
            - rewrite_reason: 改写说明
            - rewrite_round: 实际改写轮次
            - trace_log: 追加的轨迹日志
        """
        user_query = state.get("user_query", "")
        conversation_history = state.get("conversation_history", [])
        trace_log = state.get("trace_log", [])[:]

        # 调用子图
        sub_input = RewriteSubgraphState(
            user_query=user_query,
            original_query=user_query,
            conversation_history=conversation_history,
            rewrite_round=0,
            rewrite_reason="",
            clarify_context="",
            clarify_round=0,
        )
        result = rewrite_subgraph.invoke(sub_input)

        # 解析子图结果
        final_query = result.get("user_query", user_query)
        final_round = result.get("rewrite_round", 0)
        final_reason = result.get("rewrite_reason", "")

        out = {
            "user_query": final_query,
            "rewritten_query": final_query if final_round > 0 else "",
            "rewrite_reason": final_reason,
            "rewrite_round": final_round,
            "trace_log": trace_log + [f"[Rewrite] 完成 (round={final_round})"],
        }

        return out

    return rewrite_node
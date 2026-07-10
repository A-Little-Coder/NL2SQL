"""
NL2SQL 主图：串联 IR → (Clarification) → SS → AnswerabilityCheck → CG → Execution → Decision

依据 决策 22 / §18.2，决策 23 / §15.4-15.5，决策 24 / §15.6。

设计要点：
1. 节点是「适配器」，把主图 NL2SQLState 的字段映射到 Agent 子图的内部 State，
   并把子图输出再映射回主图 State。
2. 各 Agent 通过工厂 build_main_graph(config) 注入：调用方在 config 中传入
   已构造好的 InformationRetrieval / SchemaSelector / SQLGenerator /
   SQLFixLoop / SelfConsistencyDecision 实例。这样保证：
     - 现有公开 API 不变
     - 测试中可注入 Mock Agent
3. 条件边覆盖兜底：
     - IR 后无任何候选 → 主图直接 END（带 error）
     - SS 后无表 → END
     - AnswerabilityCheck 后不可回答 → END（拒答 + 原因）
     - CG 后无候选 SQL → END
     - Execution 后无成功结果时仍进入 Decision（Decision 会输出"全部失败"）
     - Decision 后结果不可信 → END（拒答 + 原因）

Clarification 节点本期占位（pass-through），Phase 2 接入。
"""

from typing import Any, Callable, Dict, List

from langgraph.graph import END, START, StateGraph
from loguru import logger

# harden-history-cache: interrupt 用于 cache_confirm 节点
try:
    from langgraph.types import interrupt
except ImportError:
    interrupt = None  # type: ignore

from src.graph.state import NL2SQLState
from src.clarification.dialog import DialogManager

# 流式 SSE 基础设施（决策 50）；导入失败时退化为静默
try:
    from src.api.streaming import (
        current_node,
        emit_safe,
        get_user_memory_ctx,
        get_session_memory_ctx,
        current_fix_loop,
    )
except Exception:  # pragma: no cover - 无 API 模块时（如离线脚本）
    current_node = None  # type: ignore

    def emit_safe(event_type, data):  # type: ignore
        return

    def get_user_memory_ctx():  # type: ignore
        return None

    def get_session_memory_ctx():  # type: ignore
        return None

    current_fix_loop = None  # type: ignore


# ---------------------------------------------------------------------------
# 节点装饰器：统一注入 SSE 事件（stage started/done + current_node ContextVar）
# ---------------------------------------------------------------------------

def _wrap_node(node_name: str, fn: Callable[[NL2SQLState], Dict[str, Any]]):
    """给节点函数包一层：进入时发 stage started，退出时发 stage done

    同时输出 [qid=<query_id>] 前缀的入口/出口/异常日志（§7b 决策 / §8.0.6 任务），
    使一次请求的所有节点执行链路在日志中可串联。
    """

    def wrapped(state: NL2SQLState) -> Dict[str, Any]:
        token = None
        if current_node is not None:
            token = current_node.set(node_name)
        qid = state.get("query_id", "") if isinstance(state, dict) else ""
        logger.info(f"[qid={qid}] [stage] node={node_name} status=started")
        emit_safe("stage", {"node": node_name, "status": "started"})
        try:
            result = fn(state) or {}
            done_payload = {"node": node_name, "status": "done"}
            # 透传节点关键字段（不含大对象）作为 stage done 摘要
            for key in ("error", "rejection_reason"):
                if key in result and result[key]:
                    done_payload[key] = result[key]
            emit_safe("stage", done_payload)
            extra = ""
            if "error" in done_payload:
                extra = f" error={done_payload['error']!r}"
            elif "rejection_reason" in done_payload:
                extra = f" rejection={done_payload['rejection_reason']!r}"
            logger.info(f"[qid={qid}] [stage] node={node_name} status=done{extra}")
            return result
        except Exception as e:
            logger.exception(f"[qid={qid}] [stage] node={node_name} error={e!r}")
            emit_safe("error", {"node": node_name, "error": str(e)})
            raise
        finally:
            if token is not None and current_node is not None:
                current_node.reset(token)

    return wrapped


# ---------------------------------------------------------------------------
# 节点工厂：每个节点把主图 state 转为 Agent 子图所需的局部 state
# ---------------------------------------------------------------------------

def make_history_cache_node(history_cache) -> Callable[[NL2SQLState], Dict[str, Any]]:
    """构造 HistoryCache 节点：检查历史命中，命中时设置 cache_hit=True 并注入 cached_sql"""

    def node(state: NL2SQLState) -> Dict[str, Any]:
        if history_cache is None:
            return {
                "cache_hit": False,
                "trace_log": state.get("trace_log", []) + ["[HistoryCache] disabled"],
            }

        session_memory = get_session_memory_ctx()
        user_memory = get_user_memory_ctx()
        conversation_history = state.get("conversation_history", [])
        metric_definitions = state.get("metric_definitions", [])

        user_id = state.get("user_id", "")
        session_id = getattr(session_memory, "session_id", "") if session_memory is not None else ""
        db_id = state.get("database_filter", "") or ""
        recalled_refs = []
        if user_id and session_id and db_id and hasattr(history_cache, "recall_session_history"):
            recalled_refs = history_cache.recall_session_history(
                state["user_query"],
                user_id=user_id,
                session_id=session_id,
                db_id=db_id,
            )

        recalled_history = [ref.to_turn() for ref in recalled_refs]
        check_history = recalled_history or conversation_history

        result = history_cache.check(
            user_query=state["user_query"],
            session_history=check_history,
            metric_definitions=metric_definitions,
        )

        # harden-history-cache：命中时也保留 historical_sql_refs，供否定回退时使用
        historical_sql_refs = [ref.to_dict() for ref in recalled_refs]

        out: Dict[str, Any] = {
            "cache_hit": result.hit,
            "cached_sql": result.cached_sql,
            "cache_source": result.source,
            "cache_confidence": result.confidence,
            "cached_historical_query": getattr(result, "historical_query", None),
            "historical_sql_refs": historical_sql_refs,
            "trace_log": state.get("trace_log", [])
                         + [f"[HistoryCache] hit={result.hit}, source={result.source}, confidence={result.confidence}, recalled={len(recalled_refs)}"],
        }
        # 决策 50：业务事件
        emit_safe("cache_check", {
            "hit": result.hit,
            "source": result.source,
            "confidence": result.confidence,
            "cached_sql": result.cached_sql,
            "recalled": len(recalled_refs),
            "historical_sql_refs": historical_sql_refs,
        })
        return out

    return node


def make_value_rewrite_node(llm_client=None) -> Callable[[NL2SQLState], Dict[str, Any]]:
    """构造 ValueRewrite 节点：比对历史查询与当前查询，改写 cached_sql 中的值参数

    Args:
        llm_client: LLM 客户端实例（应与 HistoryCache 使用同一个），None 时降级透传

    Returns:
        节点函数，输出 adjusted_cached_sql 字段
    """
    from src.memory.prompts import VALUE_REWRITE_PROMPT

    def node(state: NL2SQLState) -> Dict[str, Any]:
        qid = state.get("query_id", "")
        cached_sql = state.get("cached_sql", "")
        historical_query = state.get("cached_historical_query")
        user_query = state.get("user_query", "")

        # 降级场景：无 cached_sql、无 historical_query、无 llm_client → 直接透传
        if not cached_sql:
            return {
                "adjusted_cached_sql": None,
                "trace_log": state.get("trace_log", []) + ["[ValueRewrite] skipped (no cached_sql)"],
            }
        if not historical_query:
            return {
                "adjusted_cached_sql": cached_sql,
                "trace_log": state.get("trace_log", []) + ["[ValueRewrite] skipped (no historical_query)"],
            }
        if llm_client is None:
            return {
                "adjusted_cached_sql": cached_sql,
                "trace_log": state.get("trace_log", []) + ["[ValueRewrite] skipped (no llm_client)"],
            }

        adjusted_sql = cached_sql
        changed = False
        reason = "未变更"

        try:
            # 调用 VALUE_REWRITE_PROMPT
            messages = VALUE_REWRITE_PROMPT.format_messages(
                historical_query=historical_query,
                user_query=user_query,
                cached_sql=cached_sql,
            )
            response = llm_client.invoke(messages, as_json=True, thinking=False, run_name="value-rewrite")

            if isinstance(response, dict):
                adjusted_sql = response.get("adjusted_sql", cached_sql)
                changed = response.get("changed", False)
                reason = response.get("reason", "未变更")
        except Exception as e:
            # 异常降级：透传原 cached_sql
            logger.warning(f"[qid={qid}] [ValueRewrite] 异常降级: {e}")
            adjusted_sql = cached_sql
            changed = False
            reason = f"异常降级: {e}"

        out: Dict[str, Any] = {
            "adjusted_cached_sql": adjusted_sql,
            "trace_log": state.get("trace_log", [])
                         + [f"[ValueRewrite] changed={changed}, reason={reason}"],
        }
        emit_safe("value_rewrite", {
            "historical_query": historical_query,
            "user_query": user_query,
            "cached_sql": cached_sql,
            "adjusted_cached_sql": adjusted_sql,
            "changed": changed,
            "reason": reason,
        })
        return out

    return node


def make_cache_confirm_node() -> Callable[[NL2SQLState], Dict[str, Any]]:
    """构造 CacheConfirm 节点：向用户确认是否复用 cached_sql（或 adjusted_cached_sql）

    测试逃逸：若 state.cache_confirm_approved 已预置（非 None），则跳过 interrupt

    Returns:
        节点函数，输出 cache_confirm_approved 字段；若用户否定，则同时置 cache_hit=False 并清空 cached_sql
    """

    def node(state: NL2SQLState) -> Dict[str, Any]:
        qid = state.get("query_id", "")
        cached_sql = state.get("cached_sql", "")
        adjusted_sql = state.get("adjusted_cached_sql")
        historical_query = state.get("cached_historical_query", "")
        user_query = state.get("user_query", "")

        # 测试逃逸：已预置 approved 值，直接使用
        pre_approved = state.get("cache_confirm_approved")
        if pre_approved is not None:
            logger.info(f"[qid={qid}] [CacheConfirm] 使用预置 approved={pre_approved}")
            out: Dict[str, Any] = {
                "cache_confirm_approved": pre_approved,
            }
            if not pre_approved:
                out["cache_hit"] = False
                out["cached_sql"] = None
            return out

        # 构造确认文本：意图为主，SQL 为辅
        sql_to_show = adjusted_sql if adjusted_sql else cached_sql
        # SQL 截断：超 5 行或 200 字符
        if sql_to_show:
            lines = sql_to_show.splitlines()
            if len(lines) > 5:
                sql_to_show = "\n".join(lines[:5]) + "\n... (已截断)"
            elif len(sql_to_show) > 200:
                sql_to_show = sql_to_show[:200] + "... (已截断)"

        confirm_question = f"""检测到历史相似查询，是否复用？

历史查询: {historical_query}
当前查询: {user_query}

复用方式: 意图等价直接复用{f"（已自动改写值参数）" if adjusted_sql else ""}

待执行 SQL:
{sql_to_show}"""

        # 构造 interrupt payload（兼容 query.py 中的 clarification 事件处理）
        payload = {
            "question": confirm_question,
            "ambiguities": [],
            "round": 1,
        }

        # 调用 interrupt
        if interrupt is None:
            # 无 interrupt 时降级：自动复用（向后兼容）
            logger.warning(f"[qid={qid}] [CacheConfirm] interrupt 不可用，自动复用")
            emit_safe("cache_confirm", {
                "skipped": True,
                "reason": "interrupt unavailable",
                "approved": True,
            })
            return {"cache_confirm_approved": True}

        user_choice = interrupt(payload)

        # 解析用户选择
        approved = str(user_choice).strip() in {"复用", "reuse", "yes", "是", "1", "y", "确认", "Y", "YES"}

        out: Dict[str, Any] = {
            "cache_confirm_approved": approved,
        }
        if approved:
            out["trace_log"] = state.get("trace_log", []) + ["[CacheConfirm] 用户确认复用"]
        else:
            # 用户否定：置 cache_hit=False 并清空 cached_sql，使 single_query_graph 走完整 ir 链路
            out["cache_hit"] = False
            out["cached_sql"] = None
            out["trace_log"] = state.get("trace_log", []) + ["[CacheConfirm] 用户选择重新生成"]

        emit_safe("cache_confirm", {
            "approved": approved,
            "user_choice": str(user_choice),
            "historical_query": historical_query,
            "user_query": user_query,
        })
        return out

    return node


def make_memory_update_node(updater) -> Callable[[NL2SQLState], Dict[str, Any]]:
    """构造 MemoryUpdate 节点：自动学习用户记忆和会话记忆"""

    def node(state: NL2SQLState) -> Dict[str, Any]:
        if updater is None:
            return {
                "trace_log": state.get("trace_log", []) + ["[MemoryUpdate] disabled"],
            }

        session_memory = get_session_memory_ctx()
        user_memory = get_user_memory_ctx()

        if user_memory is not None and session_memory is not None:
            updater.update(user_memory, session_memory, state)

        return {
            "trace_log": state.get("trace_log", []) + ["[MemoryUpdate] done"],
        }

    return node


def make_run_subqueries_node(orchestrator) -> Callable[[NL2SQLState], Dict[str, Any]]:
    """构造 run_subqueries 节点（决策 14）：多意图时串行执行所有子查询

    orchestrator 为 None 或单意图时不触发（单意图走主图线性 ir→ss→...→decision）。
    """

    def node(state: NL2SQLState) -> Dict[str, Any]:
        qid = state.get("query_id", "")
        subqueries = state.get("subqueries", [])
        plan = state.get("plan_result") or {}

        # 单意图不走 orchestrator（由条件边保证，此处兜底）
        if orchestrator is None or plan.get("intent_type") != "multi" or len(subqueries) <= 1:
            return {
                "trace_log": state.get("trace_log", []) + ["[RunSubqueries] skipped (single)"],
            }

        logger.info(f"[qid={qid}] 多意图执行: {len(subqueries)} 个子查询")
        emit_safe("stage", {"node": "run_subqueries", "subqueries": subqueries})

        shared_state = {
            "database_filter": state.get("database_filter"),
            "conversation_history": state.get("conversation_history", []),
            "metric_definitions": state.get("metric_definitions", []),
            "historical_sql_refs": state.get("historical_sql_refs", []),
            "_user_memory": get_user_memory_ctx(),
        }
        results = orchestrator.run(subqueries, shared_state=shared_state)

        subquery_dicts = [r.to_dict() for r in results]
        # 多结果汇总：取第一个成功结果作为主 final_sql/final_result（aggregate_results 会做完整汇总）
        primary = next((r for r in results if r.success), None)

        emit_safe("final_decision", {
            "multi_intent": True,
            "subquery_count": len(results),
            "success_count": sum(1 for r in results if r.success),
        })

        return {
            "subquery_results": subquery_dicts,
            "final_sql": primary.final_sql if primary else "",
            "final_result": primary.final_result if primary else None,
            "final_decision": None,  # 多意图无单一 decision
            "decision_path": "MULTI",
            "trace_log": state.get("trace_log", []) + [
                f"[RunSubqueries] {sum(1 for r in results if r.success)}/{len(results)} 成功"
            ],
        }

    return node


def make_aggregate_results_node(summarizer=None) -> Callable[[NL2SQLState], Dict[str, Any]]:
    """构造 aggregate_results 节点（决策 15）：结果汇总

    - 单结果无表 → 直接透传，不调 LLM
    - 多结果 / 有表 → 调 ResultSummarizer 汇总（数据表用结构摘要降 token）
    summarizer 为 None 时退化为简单拼接。
    """

    def node(state: NL2SQLState) -> Dict[str, Any]:
        subquery_results = state.get("subquery_results", [])

        # 非多意图场景：无 subquery_results，直接透传（single 走 decision 已有 final_sql）
        if not subquery_results:
            return {
                "trace_log": state.get("trace_log", []) + ["[Aggregate] no subquery results, passthrough"],
            }

        # 单结果且无表 → 透传不调 LLM（决策 15 按需）
        if len(subquery_results) == 1 and not _has_table_data(subquery_results[0]):
            single = subquery_results[0]
            summary = single.get("final_sql", "")
            logger.info("[Aggregate] 单结果无表，透传不调 LLM")
            return {
                "summary_text": summary,
                "trace_log": state.get("trace_log", []) + ["[Aggregate] single passthrough"],
            }

        # 多结果 / 有表 → 调 summarizer
        if summarizer is not None:
            try:
                summary = summarizer.summarize(
                    subquery_results=subquery_results,
                    user_query=state.get("user_query", ""),
                )
            except Exception as e:
                logger.error(f"[Aggregate] summarizer 失败，降级拼接: {e}")
                summary = _fallback_summary(subquery_results)
        else:
            summary = _fallback_summary(subquery_results)

        return {
            "summary_text": summary,
            "trace_log": state.get("trace_log", []) + ["[Aggregate] summarized"],
        }

    return node


def _has_table_data(subquery_result: Dict[str, Any]) -> bool:
    """判断子查询结果是否为数据表（多行多列）。"""
    final_result = subquery_result.get("final_result")
    if isinstance(final_result, list):
        return len(final_result) > 0
    return False


def _fallback_summary(subquery_results: List[Dict[str, Any]]) -> str:
    """summarizer 不可用时的降级汇总（简单拼接）。"""
    parts = []
    for i, r in enumerate(subquery_results, 1):
        if r.get("success"):
            parts.append(f"子查询{i}：{r.get('subquery', '')} → {r.get('final_sql', '')}")
        else:
            parts.append(f"子查询{i}：{r.get('subquery', '')} → 失败（{r.get('error', '')}）")
    return "\n".join(parts)


def make_ir_node(retriever) -> Callable[[NL2SQLState], Dict[str, Any]]:
    """构造 IR 节点：调用 IR 子图，输出 keywords/retrieved_context"""

    def node(state: NL2SQLState) -> Dict[str, Any]:
        qid = state.get("query_id", "")
        sub = retriever.build_graph()
        result = sub.invoke({
            "user_query": state["user_query"],
            "database_filter": state.get("database_filter"),
            "conversation_history": state.get("conversation_history", []),
        })
        keywords = result.get("keywords", [])
        ctx = result.get("retrieved_context")

        # 决策 50：业务事件
        if keywords:
            emit_safe("keywords", {"groups": _serialize_keywords(keywords)})
        if ctx is not None:
            emit_safe("schema_recall", _summarize_schema(ctx))

        # §8.0.7：节点级业务摘要日志（带 qid）
        try:
            tbl_count = len(getattr(ctx, "tables", []) or []) if ctx is not None else 0
            col_count = len(getattr(ctx, "columns", []) or []) if ctx is not None else 0
        except Exception:
            tbl_count = col_count = 0
        logger.info(
            f"[qid={qid}] [IR] keywords={len(keywords)} "
            f"tables={tbl_count} columns={col_count}"
        )

        return {
            "keywords": keywords,
            "retrieved_context": ctx,
            "trace_log": state.get("trace_log", []) + ["[IR] done"],
        }

    return node


def _serialize_keywords(keywords) -> list:
    """把 IR 返回的 keywords 转为可 JSON 序列化的结构（用于 SSE 事件）"""
    out = []
    for item in keywords:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            name, expansions = item[0], item[1]
            out.append({"name": str(name), "expansions": list(expansions)})
        else:
            out.append({"name": str(item)})
    return out


def _summarize_schema(ctx) -> dict:
    """从 retrieved_context 中提取召回的列摘要（用于 SSE 事件）"""
    summary = {"groups": []}
    if ctx is None:
        return summary
    try:
        groups = getattr(ctx, "schema_results", None) or {}
        for group_name, cols in groups.items():
            top = [
                getattr(c, "column_name", str(c))
                for c in (cols or [])[:10]
            ]
            summary["groups"].append({"name": group_name, "top_columns": top})
    except Exception:
        pass
    return summary


def make_task_planner_node(task_planner, dialog_manager=None) -> Callable[[NL2SQLState], Dict[str, Any]]:
    """
    构造 TaskPlanner 节点（决策 9-13）：IR 之前的意图理解 + 三选一裁决

    三选一：
      - EXECUTE：意图清晰 → 写 subqueries，放行执行
      - CLARIFY：表述歧义 → 调 DialogManager.ask() interrupt 暂停等用户回答
                  → resume 后在节点内带澄清上下文重新 plan，直到定论或达上限/拒答
      - REJECT：拒答 → 设 rejection_reason，主图走 END

    设计：CLARIFY 的"重新裁决"在节点内完成（while 循环），不依赖条件边回环，
    避免额外的 state 字段。条件边只按 verdict 分流（reject→END / 其余→ir）。

    Args:
        task_planner: TaskPlanner 实例（None 时退化为直接 EXECUTE 单意图）
        dialog_manager: DialogManager 实例（CLARIFY 时用；None 时无法反问，CLARIFY 降级执行）
    """

    def node(state: NL2SQLState) -> Dict[str, Any]:
        qid = state.get("query_id", "")
        user_query = state["user_query"]
        round_now = state.get("clarify_round", 0)
        trace = state.get("trace_log", [])

        # task_planner 未启用 → 直接 EXECUTE 单意图（向后兼容）
        if task_planner is None:
            return {
                "plan_result": {"verdict": "execute", "intent_type": "single"},
                "subqueries": [user_query],
                "clarification_done": True,
                "trace_log": trace + ["[TaskPlanner] disabled, single execute"],
            }

        # 1. 首次裁决
        clarified = None
        plan = task_planner.plan(
            user_query=user_query,
            conversation_history=state.get("conversation_history", []),
            db_id=state.get("database_filter"),
            clarified=clarified,
        )

        # 2. CLARIFY 循环：interrupt 反问 → 带回答重新 plan，直到定论/达上限/拒答
        while plan.verdict == "clarify":
            # dialog_manager 未提供 → 无法反问，降级执行
            if dialog_manager is None:
                logger.warning(f"[qid={qid}] CLARIFY 但无 DialogManager，降级执行")
                plan = None
                break

            # 调 interrupt（首次挂起；resume 返回用户回答或 DECLINED/MAX_REACHED 信号）
            answer = dialog_manager.ask(
                clarify_question=plan.clarify_question,
                clarify_round=round_now,
                ambiguities=plan.ambiguities,
            )

            # 达上限 / 拒答 → 降级执行（用原始 query 最佳猜测）
            if DialogManager.is_declined_signal(answer):
                logger.info(f"[qid={qid}] 反问结束({answer})，降级执行")
                trace = trace + [f"[TaskPlanner] clarify ended: {answer}"]
                emit_safe("clarification", {"verdict": "clarify_ended", "signal": answer})
                plan = None
                break

            # 用户有效回答 → 轮次 +1，带回答重新 plan
            round_now += 1
            clarified = answer
            trace = trace + [f"[TaskPlanner] clarified round {round_now}: {answer[:50]}"]
            emit_safe("clarification", {"verdict": "clarify", "answer": answer, "round": round_now})
            plan = task_planner.plan(
                user_query=user_query,
                conversation_history=state.get("conversation_history", []),
                db_id=state.get("database_filter"),
                clarified=clarified,
            )

        # 3. 汇总输出
        out: Dict[str, Any] = {"clarify_round": round_now}

        # 降级执行（plan 被 None 化：无 dialog 或反问结束）
        if plan is None or plan.verdict == "clarify":
            out["plan_result"] = {"verdict": "execute", "intent_type": "single", "degraded": True}
            out["subqueries"] = [user_query]
            out["clarification_done"] = True
            out["trace_log"] = trace + ["[TaskPlanner] degraded execute"]
            return out

        out["plan_result"] = plan.to_dict()
        out["clarify_question"] = plan.clarify_question

        # REJECT → 拒答（拒答信息走 error 事件 + rejection_reason，不发 clarification）
        if plan.verdict == "reject":
            out["rejection_reason"] = plan.reject_reason
            out["error"] = f"拒答: {plan.reject_reason}"
            out["clarification_done"] = True
            out["trace_log"] = trace + [f"[TaskPlanner] reject: {plan.reject_reason}"]
            return out

        # EXECUTE → 写 subqueries 放行（意图信息随 stage done 透传，不发 clarification）
        out["subqueries"] = plan.subqueries or [user_query]
        out["clarification_done"] = True
        out["trace_log"] = trace + [
            f"[TaskPlanner] execute ({plan.intent_type}): {len(out['subqueries'])} subqueries"
        ]
        return out

    return node


def make_ss_node(selector) -> Callable[[NL2SQLState], Dict[str, Any]]:
    """构造 SS 节点：调用 SS 子图，输出 selected_schema"""

    def node(state: NL2SQLState) -> Dict[str, Any]:
        qid = state.get("query_id", "")
        ctx = state.get("retrieved_context")
        if ctx is None:
            return {"error": "IR 未产出 retrieved_context",
                    "selected_schema": []}
        sub = selector.build_graph()
        result = sub.invoke({
            "user_query": state["user_query"],
            "retrieved_context": ctx,
        })
        selected = result.get("selected_schema", [])
        try:
            tbl_count = len(selected)
            col_count = sum(len(getattr(t, "columns", []) or []) for t in selected)
        except Exception:
            tbl_count = col_count = 0
        logger.info(
            f"[qid={qid}] [SS] selected_tables={tbl_count} selected_columns={col_count}"
        )
        return {
            "selected_schema": selected,
            "trace_log": state.get("trace_log", []) + ["[SS] done"],
        }

    return node


def make_schema_finalize_node(retriever, data_dir: str = None) -> Callable[[NL2SQLState], Dict[str, Any]]:
    """
    构造 SchemaFinalize 节点（relocate-join-path-injection）。

    位于 SS 之后、answerability_check/cg 之前。基于收窄后的 selected_schema 计算
    表间 JOIN 路径，补充桥接表 M-Schema，产出 join_paths_text 写回 state。
    JOIN 注入职责从 IR 阶段迁移至此（决策 26 实现位置变更）。

    Args:
        retriever: InformationRetrieval 实例（提供 vector_store / _vectorizer）。
        data_dir: data/ 根目录，定位 schema_graphs/。None 时函数内取默认路径。
    """

    def node(state: NL2SQLState) -> Dict[str, Any]:
        qid = state.get("query_id", "")
        schema = state.get("selected_schema", [])
        database_filter = state.get("database_filter")

        join_paths_text = ""
        finalized_schema = schema

        try:
            from src.preprocessing.schema_graph_builder import enrich_schema_with_join_paths
            vector_store = getattr(retriever, "vector_store", None)
            vectorizer = getattr(retriever, "_vectorizer", None)
            finalized_schema, join_paths_text = enrich_schema_with_join_paths(
                selected_schema=schema,
                database_filter=database_filter,
                vector_store=vector_store,
                vectorizer=vectorizer,
                data_dir=data_dir,
            )
        except Exception as e:
            # 兜底：异常时 schema 原样、join_paths_text 置空，不阻断流水线
            logger.warning(f"[qid={qid}] [SchemaFinalize] 失败降级: {e}")
            finalized_schema = schema
            join_paths_text = ""

        # 统计
        edge_count = 0
        bridge_count = 0
        try:
            if join_paths_text:
                edge_count = join_paths_text.count("JOIN")
            if finalized_schema and schema:
                bridge_count = len(finalized_schema) - len(schema)
        except Exception:
            pass
        logger.info(
            f"[qid={qid}] [SchemaFinalize] join_edges={edge_count} bridge_tables={bridge_count} "
            f"has_text={bool(join_paths_text)}"
        )

        emit_safe("schema_finalize", {
            "join_edges": edge_count,
            "bridge_tables": bridge_count,
        })

        return {
            "selected_schema": finalized_schema,
            "join_paths_text": join_paths_text,
            "trace_log": state.get("trace_log", []) + ["[SchemaFinalize] done"],
        }

    return node

    return node


def make_answerability_check_node(checker) -> Callable[[NL2SQLState], Dict[str, Any]]:
    """
    构造可回答性检查节点（决策 23）：SS 之后、CG 之前

    宽松原则：只有 answerable="false" 才拦截，uncertain 放行。
    """

    def node(state: NL2SQLState) -> Dict[str, Any]:
        schema = state.get("selected_schema", [])
        ir_ctx = state.get("retrieved_context")
        result = checker.check(
            user_query=state["user_query"],
            mschema=schema,
            ir_context=ir_ctx,
        )
        out: Dict[str, Any] = {
            "answerability_result": result.to_dict(),
            "trace_log": state.get("trace_log", [])
                         + [f"[AnswerabilityCheck] {result.answerable}"],
        }
        if result.should_reject:
            out["rejection_reason"] = result.reason
            out["error"] = f"不可回答: {result.reason}"

        # 决策 50：业务事件
        emit_safe("answerability", {
            "answerable": result.answerable,
            "confidence": getattr(result, "confidence", None),
            "reason": result.reason,
        })
        return out

    return node


def make_cg_node(generator) -> Callable[[NL2SQLState], Dict[str, Any]]:
    """构造 CG 节点：调用 CG 子图，输出 sql_candidates"""

    def node(state: NL2SQLState) -> Dict[str, Any]:
        qid = state.get("query_id", "")
        schema = state.get("selected_schema", [])
        if not schema:
            return {"error": "SS 未产出 selected_schema",
                    "sql_candidates": []}
        sub = generator.build_graph()
        result = sub.invoke({
            "user_query": state["user_query"],
            "selected_schema": schema,
            "query_preferences": get_user_memory_ctx().get_query_preferences()
                                  if get_user_memory_ctx() else {},
            "metric_definitions": state.get("metric_definitions", []),
            "historical_sql_refs": state.get("historical_sql_refs", []),
            "join_paths_text": state.get("join_paths_text", ""),
        })
        candidates = result.get("sql_candidates", [])

        # 决策 50：业务事件
        emit_safe("sql_candidates", {
            "candidates": [
                {"id": getattr(c, "id", str(i)), "sql": getattr(c, "sql", str(c))}
                for i, c in enumerate(candidates)
            ],
        })

        # §8.0.7：节点级业务摘要日志（带 qid）
        logger.info(f"[qid={qid}] [CG] candidates={len(candidates)}")

        return {
            "sql_candidates": candidates,
            "trace_log": state.get("trace_log", []) + ["[CG] done"],
        }

    return node


def make_execution_node(fix_loop) -> Callable[[NL2SQLState], Dict[str, Any]]:
    """
    构造 Execution 节点（决策 51：ExecuteAll，一次性执行不修复）

    重大变更（决策 51）：
    - 5 个候选只做**一次性执行**，不在执行阶段触发任何 LLM 修复
    - 所有修复逻辑已移至 Decision 节点的 SmartFix 子流程
    - fix_loop 参数仍保留（向后兼容签名），但仅使用其 executor 字段

    当 cache_hit=True 时，从 cached_sql 构造候选并直接执行（跳过 IR/SS/CG）。
    """
    from src.sql_generation.sql_generator import SQLCandidate, SQLStatus
    from src.schema_selection.schema_selector import MSchemaFormat

    def node(state: NL2SQLState) -> Dict[str, Any]:
        qid = state.get("query_id", "")
        # 如果 history_cache 命中，从 adjusted_cached_sql（优先）或 cached_sql 构造候选
        if state.get("cache_hit", False):
            cached_sql = state.get("adjusted_cached_sql") or state.get("cached_sql", "")
            if not cached_sql:
                return {"error": "cache_hit=True 但 cached_sql 与 adjusted_cached_sql 均为空"}

            cand = SQLCandidate(
                id="cache_hit",
                sql=cached_sql,
                status=SQLStatus.PENDING,
            )
            candidates = [cand]
        else:
            candidates = state.get("sql_candidates", [])

        if not candidates:
            return {"error": "CG 未产出 sql_candidates"}

        # 准备 schema_text 供后续 SmartFix 使用（只有有 schema 时才生成）
        schema = state.get("selected_schema", [])
        try:
            if schema:
                mschema_dict = MSchemaFormat.create_mschema_schema(schema)
                schema_text = MSchemaFormat.format_for_llm(mschema_dict)
            else:
                schema_text = ""
        except Exception:
            schema_text = ""

        # 拼接表关联文本（relocate-join-path-injection）：让 SmartFix 修复 SQL 时也能看到 JOIN 关系
        join_paths_text = state.get("join_paths_text", "") or ""
        if schema_text and join_paths_text:
            schema_text = f"{schema_text}\n\n## 表关联\n{join_paths_text}"

        # 决策 51：一次性执行每个候选，不触发 LLM 修复
        executor = fix_loop.executor
        success_count = 0
        for cand in candidates:
            try:
                exec_result = executor.execute(cand.sql)
                cand.result = exec_result.result_data
                cand.execution_time = exec_result.execution_time
                cand.status = (
                    SQLStatus.SUCCESS if exec_result.success else SQLStatus.FAILED
                )
                cand.error_message = (
                    exec_result.error.original_message if exec_result.error else None
                )
                # 保留结构化错误供 SmartFix 使用
                cand.structured_error = exec_result.error if not exec_result.success else None
                if exec_result.success:
                    success_count += 1
            except Exception as e:
                cand.status = SQLStatus.FAILED
                cand.error_message = str(e)
                cand.structured_error = None

            # 决策 50：每条候选执行完 emit
            try:
                rows = len(cand.result) if isinstance(cand.result, list) else None
            except Exception:
                rows = None
            emit_safe("execution", {
                "candidate_id": getattr(cand, "id", None),
                "success": cand.status == SQLStatus.SUCCESS,
                "rows": rows,
                "error": cand.error_message,
            })

        # §8.0.7：节点级业务摘要日志（带 qid）
        logger.info(
            f"[qid={qid}] [ExecuteAll] total={len(candidates)} success={success_count} "
            f"failed={len(candidates) - success_count}"
        )

        return {
            "sql_candidates": candidates,
            "schema_text": schema_text,
            "trace_log": state.get("trace_log", []) + ["[ExecuteAll] done (no fix)"],
        }

    return node


def make_decision_node(decider, fix_loop=None) -> Callable[[NL2SQLState], Dict[str, Any]]:
    """
    构造 Decision 节点：调用 Decision 子图，输出 final_decision

    决策 51 重写：
    - 通过子图 state 注入 fix_loop（每 DB 独立的 SQLFixLoop 实例）
    - 同步 candidate_scores_r1/r2 / fix_failed / decision_path 等新字段回主图 state
    - 保留原 result_verifier 调用（已移入 Decision 子图末尾节点）

    Args:
        decider: SelfConsistencyDecision 实例
        fix_loop: SQLFixLoop 实例（决策 51；优先于 decider.fix_loop）
    """

    def node(state: NL2SQLState) -> Dict[str, Any]:
        qid = state.get("query_id", "")
        cands = state.get("sql_candidates", [])
        sub = decider.build_graph()
        sub_input = {
            "candidates": cands,
            "user_query": state["user_query"],
            "schema_text": state.get("schema_text", ""),
            "mschema": state.get("selected_schema", []),
            # 决策 12：fix_loop 不进 state（checkpointer 序列化 SQLFixLoop 会报错），
            # 改用 ContextVar 传递，decision 子图节点从 get_fix_loop_ctx() 取
        }

        # set ContextVar 供 decision 子图 SmartFix 节点使用（per-db fix_loop）
        fl_token = current_fix_loop.set(fix_loop) if fix_loop is not None else None
        try:
            result = sub.invoke(sub_input)
        finally:
            if fl_token is not None:
                current_fix_loop.reset(fl_token)
        decision = result.get("final_decision")

        out: Dict[str, Any] = {
            "final_decision": decision,
            "trace_log": state.get("trace_log", []) + ["[Decision] done"],
        }

        if decision is not None:
            out["final_sql"] = decision.selected_sql or ""
            out["final_result"] = decision.selected_result

            # 决策 51：同步评分及修复字段到主图 state
            out["candidate_scores_r1"] = decision.candidate_scores_r1 or []
            out["candidate_scores_r2"] = decision.candidate_scores_r2
            out["selected_candidate_id"] = decision.selected_candidate_id
            out["fix_failed"] = decision.fix_failed
            out["fix_rounds_used"] = decision.fix_rounds_used
            out["last_error"] = decision.last_error
            out["decision_path"] = decision.decision_path

            # §8.0.7：节点级业务摘要日志（带 qid，含 SmartFix 子流程结果）
            logger.info(
                f"[qid={qid}] [Decision] path={decision.decision_path!r} "
                f"selected_id={decision.selected_candidate_id} "
                f"fix_failed={decision.fix_failed} "
                f"fix_rounds={decision.fix_rounds_used}"
            )

            # 决策 50：业务事件
            emit_safe("final_decision", {
                "selected_id": decision.selected_candidate_id,
                "selected_sql": decision.selected_sql,
                "decision_path": decision.decision_path,
                "fix_failed": decision.fix_failed,
                "reason": decision.decision_reason,
            })

            # 决策 51：result_verification 信息同步（保留 voting_summary.verification）
            voting = decision.voting_summary or {}
            if "verification" in voting:
                out["result_verification"] = voting["verification"]
                # 如果验证不通过，写 rejection_reason
                if voting["verification"].get("should_reject"):
                    out["rejection_reason"] = (
                        f"结果不可信: {voting['verification'].get('reason', '')}"
                    )
                    out["final_sql"] = ""
                    out["final_result"] = None

            # SmartFix 失败时也清空 final_sql / final_result？
            # 决策：保留 final_sql（最佳候选 SQL），final_result 为 None，由前端基于 fix_failed=True 决定如何展示

        return out

    return node


def make_run_single_query_node(single_query_graph) -> Callable[[NL2SQLState], Dict[str, Any]]:
    """构造 run_single_query 节点（refactor-single-query-graph）

    invoke 编译好的 single_query_graph，把其产出的 partial NL2SQLState
    （final_sql / final_result / decision_path / rejection_reason / error 等）
    合并回主图 state。单意图路径、cache 命中路径共用此节点。

    单查询流水线胶水（ir/ss/cg/execution/decision 的顺序与 fail-fast）只存在于
    single_query_graph 一处，主图与 orchestrator 不再平行重写。
    """

    def node(state: NL2SQLState) -> Dict[str, Any]:
        qid = state.get("query_id", "")
        # 子图在同线程内 invoke，ContextVar（fix_loop/user_memory/session_memory）
        # 对子图各节点天然可见，无需额外传递
        out = single_query_graph.invoke(state) or {}
        logger.info(
            f"[qid={qid}] [RunSingleQuery] decision_path={out.get('decision_path')!r} "
            f"fix_failed={out.get('fix_failed', False)} has_sql={bool(out.get('final_sql'))}"
        )
        return out

    return node


# ---------------------------------------------------------------------------
# 主图构建
# ---------------------------------------------------------------------------

def build_main_graph(
    retriever,
    selector,
    generator,
    fix_loop,
    decider,
    *,
    answerability_checker=None,
    history_cache=None,
    memory_updater=None,
    task_planner=None,
    dialog_manager=None,
    checkpointer=None,
    orchestrator=None,
    summarizer=None,
    single_query_graph=None,
    llm_client=None,
):
    """
    构造并编译 NL2SQL 主图（refactor-single-query-graph 后瘦身版 + harden-history-cache）

    主图只负责「分流 + 记忆」：history_cache → (value_rewrite → cache_confirm / task_planner) →
    (run_single_query | run_subqueries | END) → memory_update。
    单查询流水线 ir/ss/cg/execution/decision 已下沉到 single_query_graph。

    Args:
        retriever/selector/generator/fix_loop/decider: Agent 实例（用于在
            single_query_graph 未传入时内部编译；显式传入 single_query_graph 时仍需保留以兼容）
        answerability_checker: AnswerabilityChecker 实例（决策 23，可选）
        history_cache: HistoryCache 实例（决策 30，可选；None 时跳过）
        memory_updater: MemoryUpdater 实例（决策 29，可选；None 时跳过）
        task_planner: TaskPlanner 实例（决策 9，可选；None 时退化为直接 EXECUTE 单意图）
        dialog_manager: DialogManager 实例（决策 12/13，可选；CLARIFY 反问用）
        checkpointer: LangGraph checkpointer（决策 12；interrupt 必需，None 时不启用持久化）
        orchestrator: SubqueryOrchestrator 实例（决策 14，可选；多意图串行编排）
        summarizer: ResultSummarizer 实例（决策 15，可选；多结果汇总）
        single_query_graph: 已编译的单查询流水线图（refactor-single-query-graph）。
            None 时内部用 retriever/selector/... 自动编译，向后兼容旧调用方式。
        llm_client: LLM 客户端实例（供 value_rewrite 使用，应与 HistoryCache 同一个）

    Returns:
        CompiledGraph: 已编译主图，可调用 .invoke(initial_state)
    """
    # single_query_graph 未显式注入时，内部编译（向后兼容 build_main_graph(r,s,g,f,d) 旧调用）
    if single_query_graph is None:
        from src.graph.single_query_graph import build_single_query_graph
        single_query_graph = build_single_query_graph(
            retriever=retriever,
            selector=selector,
            generator=generator,
            fix_loop=fix_loop,
            decider=decider,
            answerability_checker=answerability_checker,
        )

    graph = StateGraph(NL2SQLState)

    # 节点：历史命中检测（START 之后）
    graph.add_node("history_cache", _wrap_node("history_cache", make_history_cache_node(history_cache)))
    # 节点：ValueRewrite（harden-history-cache：值参数改写）
    graph.add_node("value_rewrite", _wrap_node("value_rewrite", make_value_rewrite_node(llm_client)))
    # 节点：CacheConfirm（harden-history-cache：用户确认复用）
    graph.add_node("cache_confirm", _wrap_node("cache_confirm", make_cache_confirm_node()))
    # 节点：TaskPlanner 意图理解（决策 9，IR 之前）
    graph.add_node("task_planner", _wrap_node("task_planner", make_task_planner_node(task_planner, dialog_manager)))
    # 节点：单查询流水线（ir/ss/cg/execution/decision 下沉至此，refactor-single-query-graph）
    graph.add_node("run_single_query", _wrap_node("run_single_query", make_run_single_query_node(single_query_graph)))

    # 多意图编排 + 结果总结（决策 14/15）
    graph.add_node("run_subqueries", _wrap_node("run_subqueries", make_run_subqueries_node(orchestrator)))
    graph.add_node("aggregate_results", _wrap_node("aggregate_results", make_aggregate_results_node(summarizer)))

    # 记忆自动学习（流水线之后，END 之前）
    graph.add_node("memory_update", _wrap_node("memory_update", make_memory_update_node(memory_updater)))

    # 入口 → history_cache
    graph.add_edge(START, "history_cache")

    # HistoryCache 条件分支（harden-history-cache）：
    #   命中 → value_rewrite → cache_confirm → run_single_query
    #   未命中 → task_planner
    def route_after_cache(state: NL2SQLState) -> str:
        if state.get("cache_hit", False):
            return "value_rewrite"
        return "task_planner"

    graph.add_conditional_edges(
        "history_cache",
        route_after_cache,
        {"task_planner": "task_planner", "value_rewrite": "value_rewrite"},
    )

    # value_rewrite → cache_confirm
    graph.add_edge("value_rewrite", "cache_confirm")
    # cache_confirm → run_single_query
    graph.add_edge("cache_confirm", "run_single_query")

    # TaskPlanner 三选一条件分支（决策 9/14）：
    #   REJECT → END（拒答）
    #   EXECUTE single → run_single_query（单查询流水线）
    #   EXECUTE multi → run_subqueries（多意图 orchestrator 串行编排）
    def route_after_planner(state: NL2SQLState) -> str:
        plan = state.get("plan_result") or {}
        if plan.get("verdict") == "reject":
            return END
        if plan.get("intent_type") == "multi" and len(state.get("subqueries", [])) > 1:
            return "run_subqueries"
        return "run_single_query"

    graph.add_conditional_edges(
        "task_planner",
        route_after_planner,
        {"run_single_query": "run_single_query", "run_subqueries": "run_subqueries", END: END},
    )

    # 单查询路径：run_single_query → memory_update
    graph.add_edge("run_single_query", "memory_update")
    # 多意图路径：run_subqueries → aggregate_results → memory_update（决策 14/15）
    graph.add_edge("run_subqueries", "aggregate_results")
    graph.add_edge("aggregate_results", "memory_update")
    graph.add_edge("memory_update", END)

    # 决策 12：启用 task_planner 反问时必须配 checkpointer（interrupt 恢复依赖它）
    if checkpointer is not None:
        return graph.compile(checkpointer=checkpointer).with_config(run_name="nl2sql-pipeline")
    return graph.compile().with_config(run_name="nl2sql-pipeline")

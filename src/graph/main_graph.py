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

from typing import Any, Callable, Dict

from langgraph.graph import END, START, StateGraph
from loguru import logger

from src.graph.state import NL2SQLState

# 流式 SSE 基础设施（决策 50）；导入失败时退化为静默
try:
    from src.api.streaming import current_node, emit_safe
except Exception:  # pragma: no cover - 无 API 模块时（如离线脚本）
    current_node = None  # type: ignore

    def emit_safe(event_type, data):  # type: ignore
        return


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

        session_memory = state.get("_session_memory")
        user_memory = state.get("_user_memory")
        conversation_history = state.get("conversation_history", [])
        metric_definitions = state.get("metric_definitions", [])

        result = history_cache.check(
            user_query=state["user_query"],
            session_history=conversation_history,
            metric_definitions=metric_definitions,
        )

        out: Dict[str, Any] = {
            "cache_hit": result.hit,
            "cached_sql": result.cached_sql,
            "cache_source": result.source,
            "cache_confidence": result.confidence,
            "trace_log": state.get("trace_log", [])
                         + [f"[HistoryCache] hit={result.hit}, source={result.source}, confidence={result.confidence}"],
        }
        # 决策 50：业务事件
        emit_safe("cache_check", {
            "hit": result.hit,
            "source": result.source,
            "confidence": result.confidence,
            "cached_sql": result.cached_sql,
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

        session_memory = state.get("_session_memory")
        user_memory = state.get("_user_memory")

        if user_memory is not None and session_memory is not None:
            updater.update(user_memory, session_memory, state)

        return {
            "trace_log": state.get("trace_log", []) + ["[MemoryUpdate] done"],
        }

    return node


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


def make_clarification_node() -> Callable[[NL2SQLState], Dict[str, Any]]:
    """
    Clarification 节点占位（Phase 2 接入）

    本期默认设置 clarification_done=True 让流程继续；
    Phase 2 时替换为 ClarificationAgent.build_graph() 调用。
    """

    def node(state: NL2SQLState) -> Dict[str, Any]:
        return {
            "clarification_done": True,
            "trace_log": state.get("trace_log", []) + ["[Clarification] skipped"],
        }

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
            "query_preferences": state.get("_user_memory").get_query_preferences()
                                  if state.get("_user_memory") else {},
            "metric_definitions": state.get("metric_definitions", []),
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
        # 如果 history_cache 命中，从 cached_sql 构造候选
        if state.get("cache_hit", False):
            cached_sql = state.get("cached_sql", "")
            if not cached_sql:
                return {"error": "cache_hit=True 但 cached_sql 为空"}

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
        }
        if fix_loop is not None:
            sub_input["fix_loop"] = fix_loop

        result = sub.invoke(sub_input)
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
):
    """
    构造并编译 NL2SQL 主图

    Args:
        retriever: InformationRetrieval 实例（须实现 build_graph()）
        selector: SchemaSelector 实例
        generator: SQLGenerator 实例
        fix_loop: SQLFixLoop 实例
        decider: SelfConsistencyDecision 实例
        answerability_checker: AnswerabilityChecker 实例（决策 23，可选）
        history_cache: HistoryCache 实例（决策 30，可选；None 时跳过）
        memory_updater: MemoryUpdater 实例（决策 29，可选；None 时跳过）

    Returns:
        CompiledGraph: 已编译主图，可调用 .invoke(initial_state)
    """
    graph = StateGraph(NL2SQLState)

    # 新增节点：历史命中检测（START 之后，IR 之前）
    graph.add_node("history_cache", _wrap_node("history_cache", make_history_cache_node(history_cache)))
    graph.add_node("ir", _wrap_node("ir", make_ir_node(retriever)))
    graph.add_node("clarification", _wrap_node("clarification", make_clarification_node()))
    graph.add_node("ss", _wrap_node("ss", make_ss_node(selector)))

    # 可回答性检查节点（决策 23）：SS 之后、CG 之前
    if answerability_checker is not None:
        graph.add_node(
            "answerability_check",
            _wrap_node(
                "answerability_check",
                make_answerability_check_node(answerability_checker),
            ),
        )

    graph.add_node("cg", _wrap_node("cg", make_cg_node(generator)))
    graph.add_node("execution", _wrap_node("execution", make_execution_node(fix_loop)))
    graph.add_node("decision", _wrap_node("decision", make_decision_node(decider, fix_loop=fix_loop)))

    # 新增节点：记忆自动学习（decision 之后，END 之前）
    graph.add_node("memory_update", _wrap_node("memory_update", make_memory_update_node(memory_updater)))

    # 入口 → history_cache
    graph.add_edge(START, "history_cache")

    # HistoryCache 条件分支：命中 → execution（复用缓存 SQL）；否则 → ir
    def route_after_cache(state: NL2SQLState) -> str:
        if state.get("cache_hit", False):
            return "execution"
        return "ir"

    graph.add_conditional_edges(
        "history_cache",
        route_after_cache,
        {"ir": "ir", "execution": "execution"},
    )
    graph.add_edge("ir", "clarification")

    # Clarification 条件分支：done → SS，否则循环回自身（Phase 2 实装）
    def route_after_clarification(state: NL2SQLState) -> str:
        return "ss" if state.get("clarification_done", True) else "clarification"

    graph.add_conditional_edges(
        "clarification",
        route_after_clarification,
        {"ss": "ss", "clarification": "clarification"},
    )

    # SS 后：无 schema → END；有 schema → answerability_check 或 CG
    if answerability_checker is not None:
        def route_after_ss(state: NL2SQLState) -> str:
            if not state.get("selected_schema"):
                return END
            return "answerability_check"

        graph.add_conditional_edges(
            "ss", route_after_ss,
            {"answerability_check": "answerability_check", END: END},
        )

        # 可回答性检查条件分支（决策 23）：false → END（拒答），否则 → CG
        def route_after_answerability(state: NL2SQLState) -> str:
            check = state.get("answerability_result")
            if check and check.get("answerable") == "false":
                return END
            return "cg"

        graph.add_conditional_edges(
            "answerability_check",
            route_after_answerability,
            {"cg": "cg", END: END},
        )
    else:
        def route_after_ss(state: NL2SQLState) -> str:
            return "cg" if state.get("selected_schema") else END

        graph.add_conditional_edges(
            "ss", route_after_ss, {"cg": "cg", END: END},
        )

    # CG 后：无候选直接结束
    def route_after_cg(state: NL2SQLState) -> str:
        return "execution" if state.get("sql_candidates") else END

    graph.add_conditional_edges(
        "cg", route_after_cg, {"execution": "execution", END: END}
    )

    graph.add_edge("execution", "decision")
    graph.add_edge("decision", "memory_update")
    graph.add_edge("memory_update", END)

    return graph.compile().with_config(run_name="nl2sql-pipeline")

> **修订注（值参数泛化）**：初版 date-only 改写已实现并勾选；本期修订将改写范围泛化为「任意值参数（WHERE 谓词值 / LIMIT / HAVING 值）」，cache_check 规则3 同步泛化。泛化-重做已完成：`date_rewrite` -> `value_rewrite`，全量 658 passed、0 failed，`validate --strict` 通过，无 date_rewrite 残留。

## 1. 写入闸门收紧（点1：报错 SQL 不存）

- [x] 1.1 `src/api/routes/query.py` `_should_write_session_turn` 写入条件由 `bool(final_sql)` 改为 `bool(final_sql) and not accumulated.get("fix_failed")`
- [x] 1.2 修正 `tests/api/test_session_write_semantics.py::test_smartfix_failed_not_written`：accumulated 改为 `final_sql` 非空 + `fix_failed=True`（反映 decision_graph.py:289 运行时真实现状），断言不入会话
- [x] 1.3 新增 `test_fixfailed_nonempty_sql_not_written`：显式覆盖「SmartFix 失败但 final_sql 非空」盲区
- [x] 1.4 复核 `_should_write_session_turn` 其余场景测试（拒答 / fail-fast / 成功 / interrupt）仍通过

## 2. CacheResult 扩展与 check() 改造（点3 数据前提）

- [x] 2.1 `src/memory/history_cache.py` `CacheResult` 新增 `historical_query: Optional[str]` 字段
- [x] 2.2 `check()` 命中时回填命中的 `historical_query`（据 LLM 返回的 `matched_turn_index` 从入参 session_history 中定位原 query，避免 LLM 改写）
- [x] 2.3 `make_history_cache_node`（main_graph.py）将 `historical_query` 注入 state（新字段 `cached_historical_query`）
- [x] 2.4 `src/graph/state.py` 新增 `cached_historical_query` 字段并纳入 `create_initial_state` 默认值
- [x] 2.5 **[回填加固]** `_parse_response` 回填 `historical_query` 增加 `cached_sql` 反查兜底：`matched_turn_index` 不准/缺失时（LLM 多候选时不稳定），按 `cached_sql` 精确匹配 session_history 的 `final_sql` 回填（端到端验证发现：`historical_query=None` 会让 value_rewrite 走 `if not historical_query` 降级、改写不生效）

## 3. CACHE_CHECK_PROMPT 规则3 调整（泛化：仅值参数变化可复用）

- [x] 3.1 **[泛化-重做]** `src/memory/prompts.py` `CACHE_CHECK_PROMPT` 规则3 由「时间变化可复用」改为「仅值参数（WHERE 谓词值 / LIMIT / HAVING 值）变化可复用，交 value_rewrite 改写；结构/意图/增删谓词变化不复用」
- [x] 3.2 **[泛化-重做]** 更新 `tests/memory/test_history_cache.py`：时间变化场景扩为通用值变化场景（日期/地区/产品/阈值/LIMIT 命中；增删谓词/GROUP BY/指标变化不命中）
- [x] 3.3 **[泛化-重做]** 复核 cache-check 既有命中/未命中/置信度场景测试在泛化规则3 下仍通过

## 4. value_rewrite 节点（点3：值参数改写，置主图，见 design D7 修订）

- [x] 4.1 **[泛化-重做]** `src/memory/prompts.py` `DATE_REWRITE_PROMPT` 改名为 `VALUE_REWRITE_PROMPT` 并泛化：diff `historical_query` vs `user_query` 的值参数差异，改写 `cached_sql` 中已存在的 WHERE 谓词值 / LIMIT / HAVING 值（含多值同变），约束「不动结构、不增删谓词」，异常/无差异原样返回
- [x] 4.2 **[泛化-重做]** `src/graph/main_graph.py` `make_date_rewrite_node` 改名为 `make_value_rewrite_node(llm_client)`：调 `VALUE_REWRITE_PROMPT` 产出 `adjusted_cached_sql`，异常/解析失败降级透传原 `cached_sql`
- [x] 4.3 `src/graph/state.py` 新增 `adjusted_cached_sql` 字段并纳入默认值（字段名不变，复用）
- [x] 4.4 **[泛化-重做]** `make_value_rewrite_node` 节点 trace_log 与 `emit_safe("value_rewrite", ...)` 业务事件（原 date_rewrite 事件名同步改）

## 5. cache_confirm 节点（点2：复用前反问，置主图直接节点）

- [x] 5.1 `src/graph/main_graph.py` 新增 `make_cache_confirm_node()`：经 `interrupt()`（`from langgraph.types import interrupt`，参照 `src/clarification/dialog.py:98`）展示历史 query / 当前 query / 复用方式（主）与 `adjusted_cached_sql`（辅），二元选择 `[复用]` / `[重新生成]`
- [x] 5.2 `src/graph/state.py` 新增 `cache_confirm_approved: Optional[bool]` 字段并纳入默认值
- [x] 5.3 复用既有 interrupt 管道：interrupt payload 形如 `{"question": <确认文本>, "ambiguities": [], "round": 1}` 以兼容 `query.py:200-211` 的 clarification 事件处理；resume 时 `interrupt()` 返回用户选择字符串；与 TaskPlanner 反问分支互斥（命中不进 planner）
- [x] 5.4 测试逃逸：`cache_confirm_approved` 已预置（非 None）时跳过 interrupt、按预置值路由；长 SQL 超 5 行或 200 字符截断，意图说明置顶

## 6. 主图命中路径路由接入（value_rewrite + cache_confirm 置主图，见 design D1）

- [x] 6.1 **[泛化-重做]** `build_main_graph`：history_cache 条件边由「hit->run_single_query」改为「hit->value_rewrite」；新增 `value_rewrite` -> `cache_confirm` -> `run_single_query` 边；value_rewrite/cache_confirm 用 `_wrap_node` 注册为主图直接节点（原 date_rewrite 节点名同步改 value_rewrite）
- [x] 6.2 `make_cache_confirm_node`：认同时保持 `cache_hit=True`（`adjusted_cached_sql` 已就位）；否定时置 `cache_hit=False`（并清空 `cached_sql`），使 `run_single_query` 走完整 ir 链路
- [x] 6.3 `single_query_graph` 入口短路保持 `cache_hit=True` -> `execution`；`execution` 节点 cache_hit 分支改用 `adjusted_cached_sql`（缺失回退 `cached_sql`）构造候选
- [x] 6.4 `single_query_graph` 入口：`cache_hit=True` 但 `cached_sql` 与 `adjusted_cached_sql` 均空 -> END（写 `error`）
- [x] 6.5 更新 `tests/graph/test_single_query_graph.py::test_cache_hit_short_circuits_schema_finalize` 等既有命中路由测试（cache_hit -> execution 仍成立，SQL 来源为 adjusted_cached_sql/cached_sql）
- [x] 6.6 更新 `tests/clarification/test_subquery_orchestrator.py::test_cache_hit_short_circuit` / `test_cache_hit_empty_sql` 以反映「主图 value_rewrite/cache_confirm + 子图 execution 短路」
- [x] 6.7 **[接线修复]** `src/api/db_pool.py` `_build` 调用 `build_main_graph` 补传 `llm_client=g.llm_client`（端到端服务验证发现：漏传导致 value_rewrite 节点恒走 `llm_client=None` 降级、改写从未生效；既有单元测试因 `_make_pool` 整体 mock `_build` 而绕过，属集成盲区）

## 7. main_graph 历史弱参考保留（点2 否定回退支持）

- [x] 7.1 `make_history_cache_node` 命中分支不再清空 `historical_sql_refs`（原 `[] if result.hit else [...]` 改为始终保留召回 refs）
- [x] 7.2 复核 cg 节点在否定回退路径下能消费 `historical_sql_refs`（sql_generator.py:245-249 / cg_graph.py:91 已消费 top-3 refs）

## 8. 测试

- [x] 8.1 **[泛化-重做]** `tests/graph/test_value_rewrite_node.py`（原 test_date_rewrite_node.py 改名）：WHERE 值变化改写 / LIMIT 值变化改写 / HAVING 值变化改写 / 多值同变 / 值一致透传 / 值非字面量透传 / 异常降级 / 无 historical_query 透传 / 无 llm_client 降级
- [x] 8.2 `tests/graph/test_cache_confirm_node.py`：预置 approved 跳过 interrupt / 认同保持 cache_hit=True / 否定置 cache_hit=False / interrupt payload 含意图+SQL
- [x] 8.3 `tests/graph/test_single_query_graph.py` 命中路径：cache_hit -> execution（用 adjusted_cached_sql）与否定 cache_hit=False -> ir 与 cached_sql 空 END
- [x] 8.4 **[泛化-重做]** e2e（`tests/graph/test_main_graph.py`）：把日期场景扩为通用值场景（地区/产品/阈值/LIMIT 命中->改写->确认；否定回退 ir；未命中不走 value_rewrite）
- [x] 8.5 回归：未命中路径（ir/ss/cg/...）行为不变；写入闸门各场景通过（泛化重做后需重跑）
- [x] 8.6 **[接线修复]** 新增 `tests/api/test_db_pool.py::test_build_passes_llm_client_to_main_graph`：mock `_build` 内重组件、专测 `build_main_graph` 收到 `llm_client=g.llm_client`，防再次漏传导致 value_rewrite 降级（填补 6.7 暴露的集成盲区）
- [x] 8.7 **[回填加固]** 更新 `tests/memory/test_history_cache.py`：`test_matched_turn_index_not_found` 改为 `test_matched_turn_index_not_found_falls_back_to_sql`（测 cached_sql 兜底回填成功）+ 新增 `test_matched_turn_index_and_sql_both_not_found`（测兜底全失败 -> None）

## 9. 验证

- [x] 9.1 **[泛化-重做]** 全量测试通过（`pytest` 658 passed, 0 failed；12 既有失败已由 fix-stale-tests 归零）
- [x] 9.2 **[泛化-重做]** `openspec validate harden-history-cache --strict` 通过
- [x] 9.3 复核 SSE 事件类型与 interrupt 恢复行为与 TaskPlanner 反问一致（cache_confirm 复用 clarification 形态 payload，query.py 处理器不改，resume 走 Command(resume=)）-- value_rewrite 改名仅影响 emit_safe 事件名，不影响 interrupt/resume 行为

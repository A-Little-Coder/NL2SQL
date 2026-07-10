## Why

HistoryCache 命中复用历史 SQL 时存在三类安全缺口，且彼此叠加放大：

1. **报错 SQL 仍被写入并反复复用**：决策路径 E（SmartFix 全失败）下 `decision_graph.py:289` 将报错的 `selected.sql` 作为 `final_sql` 写出（非空），而写入闸门 `_should_write_session_turn` 仅判断 `bool(final_sql)`，于是报错 SQL 照样入会话；cache 命中复用该 SQL 失败后又被回写，形成“错 SQL 强化循环”。现有测试 `test_smartfix_failed_not_written` 因手工构造 `final_sql=""` 而通过，掩盖了运行时 `final_sql` 非空的真实现状。
2. **复用无人工确认**：命中即短路执行，语义错误的 SQL（能执行但答非所问）被原样执行并回写强化，用户无任何拦截机会。
3. **值参数变化一律不复用**：现 prompt 规则 3“涉及时间范围变化 -> 不复用”既丢失复用机会，又无值参数改写能力，“去年的销售额”与“今年的销售额”、“华东区”与“华北区”、“前10”与“前20”等仅值参数不同的等价查询都被拒之门外。

本变更以最小改动闭合上述三类缺口，把机械错误挡在写入闸门、语义对错交给用户、值参数变化交给专门改写节点。

## What Changes

- **写入闸门收紧**：会话历史写入条件由“`final_sql` 非空”扩展为“`final_sql` 非空且 `fix_failed` 为假”，阻止 SmartFix 失败（执行报错）的 SQL 入会话；同步修正 `test_smartfix_failed_not_written` 使其反映运行时 `final_sql` 非空的真实现状。
- **复用前反问确认**：history_cache 命中后、执行前新增 `cache_confirm` 中断节点，向用户展示意图等价性（历史 query / 当前 query / 复用方式，作为判断依据）与对应 SQL（补充透明度），二元选择 `[复用]` / `[重新生成]`；否定时回退正常 ir/ss/cg 链路并保留 `historical_sql_refs` 供生成阶段参考。
- **值参数改写节点**：cache 命中路径新增 `value_rewrite` 节点（位于 confirm 之前），由 LLM 比对历史 query 与当前 query、改写 `cached_sql` 中已存在的值参数（WHERE 谓词值 / LIMIT / HAVING 值，含多值同变），约束不动结构、不增删谓词；`CacheResult` 扩展携带 `historical_query` 以供比对；原 prompt 规则 3“时间范围变化 -> 不复用”废止，改为「仅值参数变化可复用，交 value_rewrite 改写；结构/意图/增删谓词变化不复用」。
- **路由调整**：主图 cache 命中路径由 `cache_hit -> run_single_query(execution)` 调整为 `cache_hit -> value_rewrite -> cache_confirm -> run_single_query`（认同短路 execution / 否定回退 ir）。

## Capabilities

### New Capabilities
- `history-cache-reuse`: 历史缓存复用语义--复用决策（can_reuse / confidence / 携带 historical_query、仅值参数变化可复用）、值参数改写阶段（value_rewrite）、复用前人工确认阶段（cache_confirm，含否定回退）。

### Modified Capabilities
- `single-query-pipeline`: “History Cache Hit Short-Circuit” 要求的路由变更--命中后经主图 `value_rewrite -> cache_confirm` 再到 `run_single_query`（子图短路 execution），或确认否定时回退 `ir`，而非直接短路 `execution`。
- `session-memory-write-semantics`: “Unsuccessful Turns Not Written” 要求收紧--SmartFix 失败（`fix_failed` 为真）的轮次即使 `final_sql` 非空也不入会话。

## Impact

- **代码**：
  - `src/memory/history_cache.py`：`CacheResult` 扩展 `historical_query`；`check()` 返回值携带命中历史 query。
  - `src/memory/prompts.py`：`CACHE_CHECK_PROMPT` 规则 3 泛化（仅值参数变化可复用，交 value_rewrite）；新增 `VALUE_REWRITE_PROMPT`（改写 WHERE/LIMIT/HAVING 值参数）。
  - `src/graph/main_graph.py`：新增 `make_value_rewrite_node` / `make_cache_confirm_node` 节点工厂与主图命中路由（`history_cache -> value_rewrite -> cache_confirm -> run_single_query`，含否定回退 `ir`）；`make_history_cache_node` 命中分支保留 `historical_sql_refs`。
  - `src/graph/single_query_graph.py`：入口短路 `cache_hit -> execution` 改用 `adjusted_cached_sql`（缺失回退 `cached_sql`）。
  - `src/graph/state.py`：新增 `adjusted_cached_sql` / `cached_historical_query` / `cache_confirm_approved` 字段。
  - `src/api/routes/query.py`：`_should_write_session_turn` 收紧为 `bool(final_sql) and not fix_failed`。
- **测试**：修正 `tests/api/test_session_write_semantics.py` 盲区；新增 value_rewrite / cache_confirm 节点单测；新增 cache 命中反问否定回退 e2e。
- **依赖**：复用既有 interrupt 基础设施（`clarify_question` / `__interrupted__` / `clarify_round`），cache 命中分支与 TaskPlanner 反问分支互斥，不冲突。

## Context

HistoryCache 命中复用是 `single_query_graph` 的一条短路路径：`cache_hit == True` 时跳过 ir/ss/schema_finalize/cg 直奔 execution。当前实现存在三类缺口（详见 proposal.md）：

- 写入闸门 `_should_write_session_turn`（query.py:55-57）仅判 `bool(final_sql)`，而 `decision_graph.py:287-289` 在 SmartFix 失败时把报错的 `selected.sql` 作为 `final_sql` 写出（非空），导致报错 SQL 入会话并被 cache 反复复用强化。
- 命中即执行，无人工确认，语义错 SQL 原样跑、原样回写。
- prompt 规则 3「时间变化 -> 不复用」一刀切拒绝，丢失复用机会；且命中后无值参数改写能力（日期/地区/产品/阈值/LIMIT 等 WHERE/LIMIT 值变化都得重跑全链路）。

现有 interrupt 基础设施（`clarify_question` / `__interrupted__` / `clarify_round`，state.py:90-99）已支撑 TaskPlanner 反问；cache 命中分支与 TaskPlanner 分支在主图互斥（`route_after_cache` 命中即不进 planner），故可复用同一套 interrupt 管道而不冲突。

约束：`single_query_graph` 是「单意图 / cache 命中 / 多意图串行」三处的单一事实来源（refactor-single-query-graph），新增节点须落入主图（见 D1）。

> **修订注（泛化）**：初版 D7 仅做 date-only 改写；本期修订将改写范围泛化为「任意值参数（WHERE 谓词值 / LIMIT / HAVING 值）」，cache_check 规则3 同步泛化。date-only 从未上线，故直接修订本 change 而非另立。

## Goals / Non-Goals

**Goals:**
- 报错 SQL（SmartFix 失败）不再入会话，闭合 cache 强化循环。
- cache 命中复用前经人工确认，语义对错交用户判断。
- 值参数（WHERE 谓词值 / LIMIT / HAVING 值）变化时复用 SQL 并改写对应值，而非拒绝复用；结构/意图/增删谓词变化仍不复用。
- 复用既有 interrupt 管道与 `historical_sql_refs` 字段，不引入新基础设施。

**Non-Goals:**
- 不在写入时判定语义正确性（难问题，交用户确认兜底）。
- 不引入置信度衰减 / TTL / 分层缓存等记忆淘汰机制（本期范围外）。
- 不改动 session 召回（hybrid recall）与 metric_definition 提取逻辑。
- 不为 value_rewrite 引入 schema 依赖（纯文本改写）。

## Decisions

### D1：value_rewrite + cache_confirm 置于主图（非 single_query_graph）
命中路径由 `cache_hit -> execution` 调整为：主图 `history_cache -> value_rewrite -> cache_confirm -> run_single_query`；`run_single_query` 内子图据 `cache_hit` 短路 `execution`（认同）或走 `ir`（否定）。

**为何**：`cache_confirm` 调用 LangGraph `interrupt()`，要求所在图为配有 checkpointer 的流式顶层图。主图 `build_main_graph` 以 `checkpointer` 编译（main_graph.py:995-996），且 task_planner 反问已是主图直接节点（interrupt 可正常 resume）。而 `single_query_graph` 在 `run_single_query` 节点内经 `single_query_graph.invoke(state)` 命令式调用（main_graph.py:869），其自身编译无 checkpointer（single_query_graph.py:124），`interrupt()` 在其中触发后 resume 值无法回传到该次 invoke、恢复时会重跑子图。故 `cache_confirm` 必须为主图直接节点；`value_rewrite` 须在其前，亦置主图。

**备选**：(a) 将 value_rewrite/cache_confirm 放 single_query_graph--否决，interrupt resume 不工作；(b) 将 single_query_graph 改为注册式子图节点（`add_node` 直接挂编译图）以让 checkpointer 下传--改动大、影响 `run_single_query` 日志包装与 orchestrator 复用，本期不做。

**不变项**：`single_query_graph` 仍为 ir/ss/cg/execution/decision 的单一事实来源；cache 命中短路由 `cache_hit -> execution`（读 `adjusted_cached_sql`）仍在子图入口；value_rewrite/cache_confirm 是主图在 `run_single_query` 之前的两个直接节点。

**测试逃逸**：`cache_confirm` 节点 SHALL 在 `cache_confirm_approved` 已预置（非 None）时跳过 interrupt、直接按预置值路由，便于单测/编程式调用绕过反问；真实用户流留 None 触发 interrupt。

### D2：反问给「意图为主 + SQL 为辅」，二元选择
`cache_confirm` interrupt 展示历史 query / 当前 query / 复用方式（判断依据）与 `adjusted_cached_sql`（补充透明度），选择 `[复用]` / `[重新生成]`。

**为何**：业务用户判得了「是不是同一个问题」，判不了 SQL 对错；意图为主保证确认有效，SQL 为辅提供透明度与审计痕。二元选择最简，否定即回退。泛化改写后 LLM 出错概率上升，这道确认更成为必需的安全网（见 D7）。

**备选**：(a) 只给 SQL 让用户判--用户看不懂，形同虚设；(b) 分层（高置信自动执行、低置信反问）--重新引入复杂度，与「简化」目标相悖。均否决。

### D3：value_rewrite 置于 cache_confirm 之前
顺序为 value_rewrite -> cache_confirm -> execution。

**为何**：用户确认的是「真正要跑的 SQL 的意图」；若先确认再改值参数，用户确认的是待会会被悄悄改动的 SQL，与实际执行不一致。否定时白跑一次 rewrite 的 LLM 调用，代价可接受。

**备选**：confirm -> value_rewrite。否决：确认与执行不一致。

### D4：CacheResult 扩展 `historical_query`
`CacheResult` 新增 `historical_query` 字段，`check()` 命中时回填命中的历史 query；`make_history_cache_node` 将其注入 state（如 `cached_historical_query`）供 value_rewrite 比对。

**为何**：value_rewrite 须比对「原 query vs 当前 query」，而召回的 `HistoricalSQLReference.historical_query` 在 `check()` 返回值中被丢弃（history_cache.py:18-23 仅含 cached_sql/source/confidence）。补此字段是 value_rewrite 的数据前提。

**备选**：value_rewrite 自行重新召回历史 query。否决：重复召回、且无法保证与命中的是同一条。

### D5：写入闸门 `bool(final_sql) and not fix_failed`
`_should_write_session_turn` 增加 `and not accumulated.get("fix_failed")`。

**为何**：`fix_failed` 已在 state（state.py:125）且 decision_graph 已写出；直接取用最小改动。比「检查候选 execution status」更简单且语义明确（fix_failed 即代表最终 SQL 报错）。

**备选**：检查 `final_result is None`。否决：`final_result` 为 None 还可能是空结果集（合法成功），误杀。

### D6：否定回退保留 `historical_sql_refs`
`make_history_cache_node` 命中分支不再清空 `historical_sql_refs`（原 main_graph.py:141 `[] if result.hit else [...]` 改为始终保留）。回退 ir 后 cg 可参考历史 query/sql。

**为何**：`historical_sql_refs` 字段本就是为「不复用时保留弱参考」设计（state.py:72）；否定回退本质是「不复用」，正好复用此字段，无需新字段。

### D7：value_rewrite 每次 hit 都调 LLM，改写任意值参数（修订：从 date-only 泛化）
命中即调 value_rewrite LLM 比对 `historical_query` 与当前 `user_query`，改写 `cached_sql` 中**已存在的值参数**（WHERE 谓词值 / LIMIT / HAVING 值，含日期/地区/产品/阈值等任意字段值），产出 `adjusted_cached_sql`；无差异时原样透传。

**改写边界（本期定）**：
- ✅ 可改写：SQL 中已存在的 WHERE 谓词值、LIMIT 值、HAVING 值（含多值同变，如「华东去年」->「华北今年」同时改两个谓词）。
- ❌ 不改写且 cache_check 判不命中：增删 WHERE 谓词（「销售额」->「华东区销售额」）、GROUP BY 维度、ORDER BY 列/方向、聚合方式、表/JOIN、指标/意图变化。
- ⚠️ 值在 SQL 中非字面量（如 `year=YEAR(CURRENT_DATE)`）时改无可改，原样透传，交 cache_confirm 用户把关。

**为何泛化**：date-only 改写风险低（日期值确定、谓词语法独特）；扩到任意值参数后 LLM 改写面变大、出错概率上升。但 cache_confirm（点2）在执行前展示改写后 SQL，用户可否决--确认是泛化的安全网。泛化扩大复用机会（更多 follow-up 命中），确认吸收额外风险，二者协同。约束仍是「只改值参数，绝不碰结构、不增删谓词」。

**为何每次都调**：简单，单一职责；是否改写由 LLM 内部判定（无差异则原样返回）。避免节点侧维护「值差异检测」规则。

**备选**：(a) 仅 date-only（初版 D7）--复用机会少，否决；(b) 节点侧先用规则判值差异再调 LLM--推迟为优化项（见 Open Questions）。

## Risks / Trade-offs

- **[反问增加墙钟时间，削弱 cache 性能收益]** -> 接受（正确性优先）；否定回退仍可借 `historical_sql_refs` 加速生成；认同路径仍省 ir/ss/cg。
- **[value_rewrite LLM 误改结构或改错值参数]** -> prompt 约束「只改 WHERE/LIMIT/HAVING 值，绝不碰结构（表/列/聚合/GROUP BY/ORDER BY/JOIN）、不增删谓词」；异常降级透传原 `cached_sql`；cache_confirm（点2）执行前展示改写后 SQL 供用户否决，兜底泛化风险。
- **[用户盲目批准错 SQL 仍被强化]** -> 不比现状差（现状不问直接复用）；点1 已挡报错 SQL；语义错是固有边界，本期不解决。
- **[cache_confirm 与 TaskPlanner 反问并发]** -> 互斥分支（命中不进 planner），不冲突；复用同一 interrupt 管道。
- **[interrupt 在 SSE 流式下的状态恢复]** -> 复用既有 TaskPlanner interrupt 管道，行为一致，不引入新恢复逻辑。
- **[长 SQL 淹没反问气泡]** -> 超 N 行折叠/截断，提供「展开」；意图说明始终置顶为主。

## Migration Plan

1. **写入闸门收紧**（行为变更）：fix_failed 轮次不再入会话（此前被错误写入）。对存量会话无追溯影响，仅影响后续写入。
2. **新增节点**：value_rewrite / cache_confirm 作为主图直接节点加入 `history_cache` -> `run_single_query` 之间；未命中路径不变。
3. **prompt 规则 3 调整**：`CACHE_CHECK_PROMPT` 由「时间变化 -> 不复用」改为「仅值参数变化可复用，交 value_rewrite 改写；结构/意图/增删谓词变化不复用」。
4. **回滚**：移除 value_rewrite / cache_confirm 节点与路由 -> 还原入口短路 `cache_hit -> execution` -> 还原 `_should_write_session_turn` 一行 -> 还原 prompt 规则 3 -> 还原 `historical_sql_refs` 清空逻辑。

## Open Questions

- **value_rewrite 调用门槛**：是否在节点侧先用轻量规则（值差异命中）判定后再调 LLM，以省无差异场景的调用？当前设计每次命中都调（D7），可后续优化。
- **多意图编排下的作用面**：cache 命中在单查询子图入口、TaskPlanner 多意图分解之前；需确认 TaskPlanner 不会与 cache 命中同请求触发（当前 `route_after_cache` 命中即跳过 planner，应无冲突，实现时复核）。
- **长 SQL 截断阈值**：反问气泡中 SQL 的行数/字符截断阈值待定（建议 5 行或 200 字符）。

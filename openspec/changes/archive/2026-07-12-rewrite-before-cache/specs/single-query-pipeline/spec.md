## MODIFIED Requirements

### Requirement: Single Query Pipeline Graph
系统 SHALL 提供一个编译好的 `single_query_graph`，以 `NL2SQLState` 为输入与输出 schema，依次执行 ir → ss → schema_finalize → answerability_check（若启用）→ cg → execution → decision 七个阶段，作为单意图路径、history_cache 命中路径与多意图串行编排三处共用的单一事实来源。`schema_finalize` 节点 SHALL 位于 `ss` 之后、`answerability_check`（或未启用时的 `cg`）之前，负责基于收窄后的 `selected_schema` 计算表间 JOIN 路径、补充桥接表 M-Schema、产出 `join_paths_text`。

### Requirement: Main Graph Slimming
主图 SHALL 移除 ir / ss / answerability_check / cg / execution / decision 六个独立节点及其间的条件边，替换为单个 `run_single_query` 节点（invoke `single_query_graph`）；主图 SHALL 移除 `route_after_cache` 分支（短路逻辑下沉到子图入口）。

**MODIFIED**: 主图入口从 `START → history_cache` 改为 `START → rewrite_node`，`rewrite_node` 之后接 `history_cache`。

### Requirement: History Cache Hit Short-Circuit
`single_query_graph` SHALL 在入口条件边识别 `cache_hit == True` 且 `cached_sql`（或 `adjusted_cached_sql`）非空，直奔 `execution` 节点（从 `adjusted_cached_sql` 构造候选，缺失时回退 `cached_sql`），随后正常进入 `decision`。`value_rewrite` 与 `cache_confirm` 阶段位于主图（`history_cache` 之后、`run_single_query` 之前），见 `history-cache-reuse` 能力；`single_query_graph` 不再承载命中确认逻辑。`cache_hit` 为 True 但 `cached_sql` 与 `adjusted_cached_sql` 均为空时 SHALL 直接 END 并在 state 中写 `error`。

**MODIFIED**: `history_cache` 节点从主图入口后移一位，改为接收重写后的 `user_query`。改写后的完整语义 query 进入 cache 命中检测，提高匹配率。

#### Scenario: rewrite 拒答不进入 cache
- **WHEN** Rewrite 节点拒答（`rejection_reason` 非空）
- **THEN** 主图 SHALL 直接 END，不进入 `history_cache`

#### Scenario: rewrite 放行后 cache 命中
- **WHEN** Rewrite 节点改写 `user_query` 后放行，且改写后的 query 匹配历史缓存
- **THEN** `history_cache` SHALL 基于改写后的 `user_query` 做命中检测，命中后走 `value_rewrite → cache_confirm → run_single_query` 路径

## REMOVED Requirements

### Requirement: Main Graph Entry at START → history_cache
**Reason**: 主图入口改为 START → rewrite，history_cache 后移一位，由 rewrite 改写后的完整 query 进入 cache 检测。
**Migration**: 主图入口边从 `graph.add_edge(START, "history_cache")` 改为 `graph.add_edge(START, "rewrite")`，`history_cache` 接在 `rewrite` 之后。
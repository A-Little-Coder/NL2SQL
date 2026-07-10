## MODIFIED Requirements

### Requirement: History Cache Hit Short-Circuit
`single_query_graph` SHALL 在入口条件边识别 `cache_hit == True` 且 `cached_sql`（或 `adjusted_cached_sql`）非空，直奔 `execution` 节点（从 `adjusted_cached_sql` 构造候选，缺失时回退 `cached_sql`），随后正常进入 `decision`。`value_rewrite` 与 `cache_confirm` 阶段位于主图（`history_cache` 之后、`run_single_query` 之前），见 `history-cache-reuse` 能力；`single_query_graph` 不再承载命中确认逻辑。`cache_hit` 为 True 但 `cached_sql` 与 `adjusted_cached_sql` 均为空时 SHALL 直接 END 并在 state 中写 `error`。

#### Scenario: history_cache 命中经主图确认后子图短路执行
- **WHEN** 主图 `history_cache` 命中（`cache_hit` True、`cached_sql` 非空），经主图 `value_rewrite`（产出 `adjusted_cached_sql`）-> `cache_confirm`（用户认同、保持 `cache_hit=True`）后进入 `run_single_query`
- **THEN** `single_query_graph` 入口条件边路由至 `execution`，不执行 ir / ss / schema_finalize / cg；`execution` 节点从 `adjusted_cached_sql` 构造候选并执行，随后进入 `decision` 产出 `final_sql`

#### Scenario: 用户否定确认回退完整链路
- **WHEN** 主图 `cache_confirm` 收到用户"重新生成"选择，置 `cache_hit=False` 后进入 `run_single_query`
- **THEN** `single_query_graph` 入口条件边路由至 `ir`，执行 ir / ss / schema_finalize / cg / execution / decision 完整链路

#### Scenario: cache 命中但 cached_sql 为空
- **WHEN** `cache_hit` 为 True 但 `cached_sql` 与 `adjusted_cached_sql` 均为空
- **THEN** `single_query_graph` SHALL 直接 END 并在 state 中写 `error`，返回的 `final_sql` 为空

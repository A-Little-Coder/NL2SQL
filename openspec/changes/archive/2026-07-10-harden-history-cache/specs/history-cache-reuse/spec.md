## ADDED Requirements

### Requirement: Cache Reuse Decision
HistoryCache SHALL 判定当前查询是否可复用历史 SQL，并在可复用时于 `CacheResult` 中同时返回 `cached_sql` 与命中的 `historical_query`。判定 SHALL 以意图等价性为准：仅值参数（WHERE 谓词值 / LIMIT / HAVING 值，含日期/地区/产品/阈值等任意字段值）变化的查询 SHALL 可复用，交值改写阶段处理；结构（表/列/聚合/GROUP BY/ORDER BY/JOIN）、增删 WHERE 谓词、或指标/意图变化的查询 SHALL NOT 复用。置信度低于 `min_confidence`（默认 0.8）时返回未命中。

#### Scenario: 意图等价命中并携带历史 query
- **WHEN** 当前查询与某条历史成功 query 意图等价、参数相同，且 LLM 判定 confidence >= 0.8
- **THEN** `CacheResult.hit` 为 True，`cached_sql` 为该历史 SQL，`historical_query` 为该历史 query，`source` 为 "session_history" 或 "metric_definition"

#### Scenario: 值参数变化仍允许复用
- **WHEN** 当前查询与历史 query 意图等价但值参数不同（如"去年"->"今年"、"华东"->"华北"、"TOP10"->"TOP20"），confidence >= 0.8
- **THEN** `CacheResult.hit` 为 True 且携带 `historical_query`，交值改写阶段改写对应值参数，而非直接拒绝复用

#### Scenario: 结构或意图变化不命中
- **WHEN** 当前查询相对历史 query 存在结构变化（增删 WHERE 谓词、GROUP BY 维度、聚合方式、表/JOIN）、或指标/意图变化（如"销售额"->"利润"）
- **THEN** `CacheResult.hit` 为 False，不复用

#### Scenario: 置信度不足不命中
- **WHEN** LLM 判定 confidence < 0.8 或意图不等价
- **THEN** `CacheResult.hit` 为 False，不复用

### Requirement: Value Rewrite Stage
主图 SHALL 在 cache 命中路径（`history_cache` 之后、`run_single_query` 之前）、复用确认之前提供 `value_rewrite` 直接节点。该节点 SHALL 以 LLM 比对 `historical_query` 与当前 `user_query`，改写 `cached_sql` 中**已存在的值参数**（WHERE 谓词值 / LIMIT / HAVING 值），产出 `adjusted_cached_sql`；多值同变时 SHALL 一并改写多个值参数。该节点 SHALL 不依赖 schema（纯文本比对与改写），且 SHALL NOT 改动 SQL 结构（表/列/聚合/GROUP BY/ORDER BY/JOIN）、SHALL NOT 增删 WHERE 谓词。当两 query 值参数一致、或无 `historical_query`、或值在 SQL 中非字面量时，`adjusted_cached_sql` 等于 `cached_sql`。

#### Scenario: 值参数变化改写
- **WHEN** `historical_query` 为"去年的华东区销售额"、当前 `user_query` 为"今年的华北区销售额"，`cached_sql` 含 `WHERE year=2024 AND region='华东'`
- **THEN** `value_rewrite` 产出 `adjusted_cached_sql` 将两个值参数改写为当前引用（如 `WHERE year=2025 AND region='华北'`），不改动其他部分

#### Scenario: LIMIT 值变化改写
- **WHEN** `historical_query` 为"销售额前10"、当前 `user_query` 为"销售额前20"，`cached_sql` 含 `LIMIT 10`
- **THEN** `value_rewrite` 产出 `adjusted_cached_sql` 将 `LIMIT 10` 改为 `LIMIT 20`，不改动其他部分

#### Scenario: 值参数一致原样透传
- **WHEN** `historical_query` 与当前 `user_query` 值参数一致
- **THEN** `adjusted_cached_sql` 等于 `cached_sql`，不改动其他部分

#### Scenario: 值非字面量原样透传
- **WHEN** `cached_sql` 中对应值为非字面量表达式（如 `WHERE year=YEAR(CURRENT_DATE)`），改无可改
- **THEN** `adjusted_cached_sql` 等于 `cached_sql`，原样透传，交 cache_confirm 用户把关

#### Scenario: value_rewrite 异常安全降级
- **WHEN** `value_rewrite` 的 LLM 调用或解析失败
- **THEN** 节点 SHALL 以原 `cached_sql` 作为 `adjusted_cached_sql` 透传，不中断命中路径

### Requirement: Reuse Confirmation Gate
主图 SHALL 在 cache 命中路径、`value_rewrite` 之后、`run_single_query` 之前提供 `cache_confirm` 中断直接节点（依赖主图 checkpointer 保证 interrupt resume）。该节点 SHALL 经 interrupt 向用户展示意图等价性（历史 query、当前 query、复用方式）与对应 SQL（`adjusted_cached_sql`），提供二元选择 `[复用]` / `[重新生成]`。用户认同时 SHALL 保持 `cache_hit=True`（`adjusted_cached_sql` 已就位）并路由至 `run_single_query`（子图短路 `execution`）；用户否定时 SHALL 置 `cache_hit=False` 并路由至 `run_single_query`（子图走完整 `ir` 链路），并保留 `historical_sql_refs` 供生成阶段参考。该节点 SHALL 在 `cache_confirm_approved` 已预置（非 None）时跳过 interrupt、按预置值路由（测试逃逸）。history_cache 命中分支 SHALL 不清空 `historical_sql_refs`，以备用户否定回退时生成阶段使用。

#### Scenario: 用户认同复用
- **WHEN** `cache_confirm` 经 interrupt 收到用户"复用"选择
- **THEN** 图路由至 `execution`，以 `adjusted_cached_sql` 构造候选并执行，随后进入 `decision`

#### Scenario: 用户否定回退完整链路
- **WHEN** `cache_confirm` 经 interrupt 收到用户"重新生成"选择
- **THEN** 图回退至 `ir`，执行 ir / ss / schema_finalize / cg / execution / decision 完整链路，`historical_sql_refs` 保留供生成阶段参考

#### Scenario: 命中分支保留历史弱参考
- **WHEN** history_cache 命中（`cache_hit` 为 True）
- **THEN** `historical_sql_refs` SHALL 不被清空，以备用户否定回退时生成阶段使用

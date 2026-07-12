## ADDED Requirements

### Requirement: HistoryCache 命中长期记忆时输出指标名

HistoryCache 命中 `source=metric_definition` 时，`CacheResult` SHALL 输出 `matched_metric_name`（命中的指标定义名称）。指标名 MUST 由后端按 `cached_sql` 与 `metric_definitions` 的 `sql_pattern` 归一化反查得到（归一化规则：strip + rstrip(";") + lower），反查失败时 `matched_metric_name` MUST 为 None。反查行为 MUST NOT 影响命中复用决策（复用仍按 `cached_sql` 执行）。

#### Scenario: 命中指标并反查到指标名
- **WHEN** HistoryCache 命中 `source=metric_definition` 且 `cached_sql` 归一化后匹配某指标的 `sql_pattern`
- **THEN** `CacheResult.matched_metric_name` 为该指标的 `name`
- **AND** 命中复用逻辑不受影响（`cached_sql` 仍用于执行）

#### Scenario: 命中指标但反查失败
- **WHEN** HistoryCache 命中 `source=metric_definition` 但 `cached_sql` 归一化后不匹配任何指标的 `sql_pattern`
- **THEN** `CacheResult.matched_metric_name` 为 None
- **AND** 命中复用逻辑不受影响

#### Scenario: 命中会话历史时不输出指标名
- **WHEN** HistoryCache 命中 `source=session_history`
- **THEN** `CacheResult.matched_metric_name` 为 None

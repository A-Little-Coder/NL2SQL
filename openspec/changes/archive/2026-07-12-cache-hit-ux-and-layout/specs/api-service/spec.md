## ADDED Requirements

### Requirement: cache_check 事件携带命中指标名

`cache_check` SSE 事件 payload SHALL 增补 `matched_metric_name` 字段。当命中来源为 `metric_definition` 时，`matched_metric_name` MUST 为命中的指标定义名称；当命中来源为 `session_history` 或未命中时，`matched_metric_name` MUST 为 null。该字段为增量字段，旧消费方忽略无害。

#### Scenario: 命中长期记忆时携带指标名
- **WHEN** HistoryCache 命中且 `source=metric_definition`，后端反查到指标名
- **THEN** `cache_check` 事件 payload 含 `matched_metric_name` 为该指标名
- **AND** `hit=true`、`source=metric_definition`

#### Scenario: 命中会话历史时指标名为 null
- **WHEN** HistoryCache 命中且 `source=session_history`
- **THEN** `cache_check` 事件 payload 含 `matched_metric_name` 为 null
- **AND** `hit=true`、`source=session_history`

#### Scenario: 未命中时指标名为 null
- **WHEN** HistoryCache 未命中
- **THEN** `cache_check` 事件 payload 含 `matched_metric_name` 为 null
- **AND** `hit=false`

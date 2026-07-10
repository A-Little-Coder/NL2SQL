## MODIFIED Requirements

### Requirement: Unsuccessful Turns Not Written to Session
API 路由在请求完成后写会话历史（`session.add_turn`）时，SHALL 仅写入产出了可执行最终 SQL 的成功轮次。被拒答（`rejection_reason` 非空）、未产出最终 SQL（`final_sql` 为空）、或 SmartFix 失败（`fix_failed` 为真，即便 `final_sql` 非空）的请求 SHALL NOT 写入会话历史。即写入条件为 `bool(final_sql) and not fix_failed`。

#### Scenario: 拒答请求不入会话
- **WHEN** 请求被 TaskPlanner 判定 REJECT（`rejection_reason` 非空，无 `final_sql`）
- **THEN** 该请求 SHALL NOT 调用 `session.add_turn`，会话历史不包含此轮

#### Scenario: 失败请求不入会话
- **WHEN** 请求经流水线后未产出 `final_sql`（fail-fast 早退）
- **THEN** 该请求 SHALL NOT 调用 `session.add_turn`

#### Scenario: SmartFix 失败但 final_sql 非空不入会话
- **WHEN** 请求经 SmartFix 全部失败（`fix_failed` 为真），`decision` 节点将报错的 `selected.sql` 作为 `final_sql` 写出（非空）
- **THEN** 该请求 SHALL NOT 调用 `session.add_turn`，即便 `final_sql` 非空

#### Scenario: 成功请求入会话
- **WHEN** 请求产出非空 `final_sql` 且 `fix_failed` 为假
- **THEN** 该请求 SHALL 调用 `session.add_turn` 写入会话历史（含 user_query / final_sql / cache_hit 等）

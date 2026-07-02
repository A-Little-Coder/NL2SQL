## ADDED Requirements

### Requirement: Unsuccessful Turns Not Written to Session
API 路由在请求完成后写会话历史（`session.add_turn`）时，SHALL 仅写入产出了最终 SQL 的轮次。被拒答（`rejection_reason` 非空）或未产出最终 SQL（`final_sql` 为空）的请求 SHALL NOT 写入会话历史。

#### Scenario: 拒答请求不入会话
- **WHEN** 请求被 TaskPlanner 判定 REJECT（`rejection_reason` 非空，无 `final_sql`）
- **THEN** 该请求 SHALL NOT 调用 `session.add_turn`，会话历史不包含此轮

#### Scenario: 失败请求不入会话
- **WHEN** 请求经流水线后未产出 `final_sql`（fail-fast 早退或 SmartFix 全失败）
- **THEN** 该请求 SHALL NOT 调用 `session.add_turn`

#### Scenario: 成功请求入会话
- **WHEN** 请求产出非空 `final_sql`
- **THEN** 该请求 SHALL 调用 `session.add_turn` 写入会话历史（含 user_query / final_sql / cache_hit 等）

### Requirement: Interrupted Turns Still Skipped
反问机制 interrupt 挂起的请求（`__interrupted__` 为真）SHALL 继续不写入会话历史（等 resume 完成后再写），本变更不改变该既有行为。

#### Scenario: 反问挂起不入会话
- **WHEN** 请求触发反问 interrupt（`accumulated.__interrupted__` 为真）
- **THEN** 该请求 SHALL NOT 调用 `session.add_turn`（行为与变更前一致）

### Requirement: No Impact to History Consumers
会话历史消费方（task_planner 的 follow-up 理解、memory_updater 的记忆学习、history_cache 的历史 SQL 召回）SHALL 仅依赖成功轮次，拦截无 SQL 轮次 SHALL NOT 影响这些消费方的功能。

#### Scenario: task_planner follow-up 仍可用
- **WHEN** 会话历史仅含成功轮次且当前查询为省略句 follow-up
- **THEN** task_planner SHALL 仍能结合历史理解 follow-up 意图

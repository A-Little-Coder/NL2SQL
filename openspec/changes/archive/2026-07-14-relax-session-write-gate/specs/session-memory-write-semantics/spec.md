# Session Memory Write Semantics Specification

## Purpose

会话记忆写入语义。定义 API 路由在请求完成后写会话历史（`session.add_turn`）的
触发条件：所有非反问挂起的轮次均写入，写入时附带 `reuse_eligible` 标记，消费方按需读时过滤。
被拒答或未产出 SQL 的轮次仍写入（改写模块需要可见），但标记为 `reuse_eligible=False`。

## MODIFIED Requirements

### Requirement: Unsuccessful Turns Not Written to Session
API 路由在请求完成后写会话历史（`session.add_turn`）时，SHALL **写入所有轮次**（除反问挂起外），
写入时 SHALL 计算并附加 `reuse_eligible` 标记。
写入条件：`__interrupted__` 为假（反问挂起不写，等 resume 完成后再写完整轮次）。
`reuse_eligible` 计算规则：`bool(final_sql) and not fix_failed and not rejection_reason`。

消费者（history_cache）SHALL 在读时按 `reuse_eligible` 过滤，仅将 `reuse_eligible=True` 的轮次纳入复用候选。

#### Scenario: 拒答请求写入但标记为不可复用
- **WHEN** 请求被 TaskPlanner 判定 REJECT（`rejection_reason` 非空，无 `final_sql`）
- **THEN** 该请求 SHALL 调用 `session.add_turn`，写入 `user_query` / `rejection_reason`，且 `reuse_eligible=False`

#### Scenario: 失败请求写入但标记为不可复用
- **WHEN** 请求经流水线后未产出 `final_sql`（fail-fast 早退）
- **THEN** 该请求 SHALL 调用 `session.add_turn`，写入 `user_query`，且 `reuse_eligible=False`

#### Scenario: SmartFix 失败写入但标记为不可复用
- **WHEN** 请求经 SmartFix 全部失败（`fix_failed` 为真），`decision` 节点将报错的 `selected.sql` 作为 `final_sql` 写出（非空）
- **THEN** 该请求 SHALL 调用 `session.add_turn`，写入 `user_query` / `final_sql` / `fix_failed=True`，且 `reuse_eligible=False`

#### Scenario: 成功请求写入且标记为可复用
- **WHEN** 请求产出非空 `final_sql` 且 `fix_failed` 为假且 `rejection_reason` 为空
- **THEN** 该请求 SHALL 调用 `session.add_turn` 写入会话历史（含 user_query / final_sql / cache_hit 等），且 `reuse_eligible=True`

### Requirement: Interrupted Turns Still Skipped
反问机制 interrupt 挂起的请求（`__interrupted__` 为真）SHALL 继续不写入会话历史（等 resume 完成后再写），本变更不改变该既有行为。

#### Scenario: 反问挂起不入会话
- **WHEN** 请求触发反问 interrupt（`accumulated.__interrupted__` 为真）
- **THEN** 该请求 SHALL NOT 调用 `session.add_turn`（行为与变更前一致）

### Requirement: No Impact to History Consumers
会话历史消费方（task_planner 的 follow-up 理解、memory_updater 的记忆学习、history_cache 的历史 SQL 复用）SHALL 按 `reuse_eligible` 读时过滤，而非依赖写时拦截。`reuse_eligible=False` 的轮次 SHALL NOT 被 history_cache 纳入复用候选。改写模块（Rewrite 子图）SHALL 能看到所有轮次（含 `reuse_eligible=False`），用于指代消解。

#### Scenario: history_cache 过滤不可复用轮次
- **WHEN** 会话历史包含 `reuse_eligible=False` 的轮次且当前查询引用"那个报错的查询"
- **THEN** history_cache SHALL 在构建复用候选时排除 `reuse_eligible=False` 的轮次，不将其 SQL 喂给 LLM 判断复用

#### Scenario: task_planner follow-up 不退化
- **WHEN** 会话历史包含失败轮次（`reuse_eligible=False`）且当前查询为省略句 follow-up
- **THEN** task_planner SHALL 仍能结合所有历史（含失败轮次）理解 follow-up 意图，不因失败轮次的存在而报错或退化

#### Scenario: 改写模块可见所有轮次
- **WHEN** 会话历史包含失败/拒答轮次且当前查询包含指代（"那个"、"换个条件"）
- **THEN** 改写模块 SHALL 能在 `conversation_history` 中看到这些轮次的 `user_query`，并正确解析指代
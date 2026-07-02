# Single Query Pipeline Specification

## Purpose

单查询流水线编排能力。定义一条编译图 `single_query_graph`，以 `NL2SQLState` 为输入/输出，
依次执行 ir → ss → answerability_check（可选）→ cg → execution → decision，对无 schema /
不可回答 / 无候选 SQL 等情形做 fail-fast 早退（END），并对 history_cache 命中做短路
（跳过 ir/ss/cg 直奔 execution）。该图作为单意图路径、cache 命中路径、多意图串行编排
三处的单一事实来源，消除主图节点链与 orchestrator 平行重写的重复胶水。

## Requirements

### Requirement: Single Query Pipeline Graph
系统 SHALL 提供一个编译好的 `single_query_graph`，以 `NL2SQLState` 为输入与输出 schema，依次执行 ir → ss → answerability_check（若启用）→ cg → execution → decision 六个阶段，作为单意图路径、history_cache 命中路径与多意图串行编排三处共用的单一事实来源。

#### Scenario: 正常单意图查询
- **WHEN** 输入 `NL2SQLState` 的 `cache_hit` 为 False 且各阶段均成功产出
- **THEN** 图依次经过 ir → ss → answerability_check → cg → execution → decision，最终在 `final_sql` 写入选定 SQL、`final_result` 写入执行结果、`decision_path` 写入决策路径标识

#### Scenario: 复用现有节点工厂
- **WHEN** 编译 `single_query_graph`
- **THEN** 图节点 SHALL 复用 `make_ir_node` / `make_ss_node` / `make_answerability_check_node` / `make_cg_node` / `make_execution_node` / `make_decision_node` 节点工厂，适配逻辑与主图原实现一致，不重复实现 Agent 子图调用胶水

### Requirement: Fail-Fast Early Exit
`single_query_graph` SHALL 对流水线中的失败情形做 fail-fast 早退（直接 END），并在 partial `NL2SQLState` 中保留已产出字段与失败原因，使调用方可据 `final_sql` / `decision_path` / `rejection_reason` / `error` 判定成败。

#### Scenario: SS 未选出 schema
- **WHEN** `ss` 阶段未产出 `selected_schema`（空列表）
- **THEN** 图经条件边直接 END，不进入 answerability_check / cg / execution / decision，返回的 state 中 `selected_schema` 为空

#### Scenario: 不可回答拦截
- **WHEN** `answerability_check` 判定 `answerable == "false"`
- **THEN** 图经条件边直接 END，返回的 state 中 `rejection_reason` 记录原因，不进入 cg / execution / decision

#### Scenario: CG 未产出候选 SQL
- **WHEN** `cg` 阶段未产出 `sql_candidates`（空列表）
- **THEN** 图经条件边直接 END，不进入 execution / decision

### Requirement: History Cache Hit Short-Circuit
`single_query_graph` SHALL 在入口条件边识别 `cache_hit == True`，跳过 ir / ss / cg 直奔 `execution` 节点（从 `cached_sql` 构造候选并执行），随后正常进入 `decision`。

#### Scenario: history_cache 命中
- **WHEN** 输入 state 的 `cache_hit` 为 True 且 `cached_sql` 非空
- **THEN** 图入口条件边路由至 `execution`，不执行 ir / ss / cg；`execution` 节点从 `cached_sql` 构造候选并执行，随后进入 `decision` 产出 `final_sql`

#### Scenario: cache 命中但 cached_sql 为空
- **WHEN** `cache_hit` 为 True 但 `cached_sql` 为空
- **THEN** `execution` 节点 SHALL 在 state 中写 `error`，图经 decision → END，返回的 `final_sql` 为空

### Requirement: Main Graph Slimming
主图 SHALL 移除 ir / ss / answerability_check / cg / execution / decision 六个独立节点及其间的条件边，替换为单个 `run_single_query` 节点（invoke `single_query_graph`）；主图 SHALL 移除 `route_after_cache` 分支（短路逻辑下沉到子图入口）。

#### Scenario: 主图单意图路径
- **WHEN** TaskPlanner 裁决为 execute 且 intent_type 为 single
- **THEN** 主图 `route_after_planner` 路由至 `run_single_query` 节点，该节点 invoke `single_query_graph` 并把其 partial state 合并回主图 state，随后进入 `memory_update`

#### Scenario: 主图多意图路径
- **WHEN** TaskPlanner 裁决为 execute 且 intent_type 为 multi 且子查询数 > 1
- **THEN** 主图 `route_after_planner` 路由至 `run_subqueries` 节点，由 orchestrator 串行 invoke `single_query_graph` 处理每个子查询

#### Scenario: 主图拒答路径
- **WHEN** TaskPlanner 裁决为 reject
- **THEN** 主图 `route_after_planner` 路由至 END，不进入 `run_single_query`

### Requirement: Orchestrator Reuse Compiled Graph
`SubqueryOrchestrator` SHALL 在构造时持有编译好的 `single_query_graph`，其 `run()` 方法对每个子查询 invoke 该图（串行，不并行），从返回的 partial `NL2SQLState` 读取 `final_sql` / `final_result` / `decision_path` / `fix_failed` / `error` 组装 `SubqueryResult`。

#### Scenario: 构造签名
- **WHEN** 构造 `SubqueryOrchestrator`
- **THEN** `__init__` 接收已编译的 `single_query_graph`（而非 6 个 Agent 实例），所有构造方（API 层 / 离线脚本 / 测试）SHALL 同步更新

#### Scenario: 串行执行与失败隔离
- **WHEN** orchestrator.run 处理 N 个子查询
- **THEN** 每个 `single_query_graph.invoke` 串行执行；单个子查询 invoke 抛异常时被捕获并记为 `decision_path="FAILED"`，不中断后续子查询

#### Scenario: 删除平行实现
- **WHEN** 重构完成
- **THEN** `src/clarification/subquery_orchestrator.py` 中的 `run_single_query()` 函数 SHALL 被删除，单查询流水线胶水逻辑只存在于 `single_query_graph` 一处

### Requirement: Invariants Preserved
重构 SHALL 保持以下不变项：多意图串行执行（不引入并行 fan-out）、`current_fix_loop` / `current_user_memory` / `current_session_memory` ContextVar 串行传递约束、SSE 事件类型与 payload、各 Agent 子图 `build_graph()` 公开签名、history_cache / memory_update / aggregate_results 节点行为。

#### Scenario: SSE 事件契约不变
- **WHEN** 重构后运行单意图与多意图查询
- **THEN** `emit_safe` 发出的 stage / keywords / schema_recall / answerability / sql_candidates / execution / final_decision / clarification 等 event_type 及其 payload 结构与重构前一致

#### Scenario: ContextVar 在子图内可见
- **WHEN** `single_query_graph` 在主图线程内被 invoke
- **THEN** 子图各节点 SHALL 能通过 `get_fix_loop_ctx()` / `get_user_memory_ctx()` / `get_session_memory_ctx()` 读到主图设置的 ContextVar 值，无需额外传递

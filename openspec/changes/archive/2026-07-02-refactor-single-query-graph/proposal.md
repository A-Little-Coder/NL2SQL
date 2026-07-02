## Why

当前单查询流水线（ir → ss → answerability_check → cg → execution → decision）存在两套平行实现：主图 `src/graph/main_graph.py` 用 `add_node` + `add_conditional_edges` 串成一条节点链，而 `src/clarification/subquery_orchestrator.py` 的 `run_single_query()` 又用 Python 顺序 invoke 各 Agent 子图重写了一遍。两套实现各自手写了「`NL2SQLState` → 各 Agent 子图 state 适配 + 顺序调用」的胶水，且 fail-fast 早退语义分散在主图条件边和 orchestrator 的 `if + return` 两处。这造成重复代码、行为漂移风险，也偏离 LangGraph 用编译图表达流程的范式。

## What Changes

- 新增 `src/graph/single_query_graph.py`：编译一份以 `NL2SQLState` 为 schema 的 `single_query_graph`，复用现有 `make_ir_node` / `make_ss_node` / `make_answerability_check_node` / `make_cg_node` / `make_execution_node` / `make_decision_node` 节点工厂，内部用条件边表达 fail-fast 早退（无 schema / 不可回答 / 无候选 → END）与 history_cache 命中短路（直奔 execution）。
- 主图瘦身：删除 ir / ss / answerability_check / cg / execution / decision 六个节点及其间的条件边，替换为单个 `run_single_query` 节点（invoke `single_query_graph`）。`route_after_cache` 移除（短路逻辑下沉到子图入口）。
- `SubqueryOrchestrator` 重构：删除 `run_single_query()` 函数；构造时持有编译好的 `single_query_graph`，`run()` 的 for 循环体改为一次 invoke 该图，从返回的 partial `NL2SQLState` 读取 `final_sql` / `decision_path` / `fix_failed` / `error` 组装 `SubqueryResult`。
- 串行执行不变：保留 orchestrator 的 Python for 循环（不改并行 `Send` fan-out），保留 `current_fix_loop` / `current_user_memory` / `current_session_memory` 等 ContextVar 的串行传递约束。
- **BREAKING（内部）**：`SubqueryOrchestrator.__init__` 签名变更——由传入 `retriever/selector/generator/fix_loop/decider/answerability_checker` 六个 Agent 实例，改为传入已编译的 `single_query_graph`。所有构造方（API 层 `deps.py` / 离线脚本 / 测试）需同步更新。

## Capabilities

### New Capabilities
- `single-query-pipeline`: 单查询流水线编排能力。定义一条编译图，以 `NL2SQLState` 为输入/输出，依次执行 ir → ss → answerability_check（可选）→ cg → execution → decision，对无 schema / 不可回答 / 无候选 SQL 等情况做 fail-fast 早退（END），并对 history_cache 命中做短路（跳过 ir/ss/cg 直奔 execution）。该图作为单意图路径、cache 命中路径、多意图串行编排三处的单一事实来源。

### Modified Capabilities
<!-- 无现有 spec 级别的行为变更；SSE 事件类型/payload、各 Agent 子图 build_graph() 签名、history_cache/memory_update/aggregate_results 行为均不变。 -->

## Impact

- 新增文件：`src/graph/single_query_graph.py`。
- 修改文件：`src/graph/main_graph.py`（删 6 节点 + 边，新增 run_single_query 节点与构造注入）、`src/clarification/subquery_orchestrator.py`（删 `run_single_query()`，改 `__init__` 与 `run()`）、`src/api/deps.py`（构造 `single_query_graph` 并注入主图与 orchestrator）。
- 测试：`tests/clarification/` 下直接调用 `run_single_query()` 的用例改为 invoke 编译图；主图集成测试验证单/多/cache 三条路径行为不变。
- 不影响：各 Agent 子图内部实现、SSE 事件契约、checkpointer/interrupt 机制、记忆学习与结果汇总节点。

## Context

NL2SQL 主图（`src/graph/main_graph.py`）用 LangGraph 把 `history_cache → task_planner → (ir → ss → answerability_check → cg → execution → decision) → memory_update` 串成一条链。其中 `ir`/`ss`/`cg`/`execution`/`decision` 节点是适配器：把主图共享态 `NL2SQLState` 映射到各 Agent 子图（InformationRetrieval / SchemaSelector / SQLGenerator / SQLFixLoop / SelfConsistencyDecision）的内部 state，再把结果写回 `NL2SQLState`。

多意图路径（决策 14）需要把 TaskPlanner 拆出的 N 个子查询逐个跑完整条单查询流水线。现有实现 `SubqueryOrchestrator.run_single_query()`（`src/clarification/subquery_orchestrator.py`）**平行重写**了一份顺序 invoke 4 个 Agent 子图 + Execution 的胶水，与主图节点链逻辑重复，且 fail-fast 早退用 `if + return` 表达、分散在两处。

约束：
- `SQLFixLoop` / `UserMemory` / `SessionMemory` 是 per-db / per-request 的 Python 对象，因 checkpointer 序列化限制不能进 state，通过 `current_fix_loop` / `current_user_memory` / `current_session_memory` 三个 ContextVar 传递。同线程串行时它们天然可见。
- 多意图执行刻意串行（保护 DB 连接池与 LLM 限流），不并行 fan-out。
- SSE 事件契约（`emit_safe` 的 event_type 与 payload）是前端依赖的对外接口，不可变。
- 各 Agent 的 `build_graph()` 公开签名是稳定 API，不可变。

## Goals / Non-Goals

**Goals:**
- 抽取一份编译好的 `single_query_graph`，以 `NL2SQLState` 为 schema，复用现有 6 个节点工厂，让主图单意图路径、history_cache 命中路径、orchestrator 多意图串行三处共用同一份流水线实现。
- fail-fast 早退（无 schema / 不可回答 / 无候选）与 cache 命中短路在图结构中显式表达（条件边 + END），不再依赖 Python `if + return`。
- 消除 `run_single_query()` 的平行重写，主图节点数从 10+ 缩减到 5。

**Non-Goals:**
- 不改并行执行：保留串行 for 循环，不引入 `Send` fan-out（ContextVar 传递与 DB/LLM 限流约束不变）。
- 不改各 Agent 子图内部实现与 `build_graph()` 签名。
- 不改 SSE 事件类型与 payload。
- 不改 checkpointer / interrupt / 记忆学习 / 结果汇总行为。
- 不重构 history_cache / memory_update / aggregate_results 节点。

## Decisions

### 决策 1：single_query_graph 以 `NL2SQLState` 为 schema，复用现有节点工厂
现有 `make_ir_node` 等节点工厂的输入输出本就是 `NL2SQLState`（适配器模式），故抽取子图**无需重写任何适配逻辑**，只需把主图中 ir/ss/answerability/cg/execution/decision 节点及其间的条件边原样平移到一个独立 `StateGraph(NL2SQLState)` 编译。

**备选**：另起一套专用的 `SingleQueryState`。否决——会引入新的 state 映射层，与主图共享态割裂，反而增加胶水。

### 决策 2：fail-fast 用条件边 + END 表达，替代 Python if+return
- `ir` → `ss`（固定边）
- `ss` 后无 `selected_schema` → END
- `answerability_check` 后 `answerable=="false"` → END
- `cg` 后无 `sql_candidates` → END
- `execution` → `decision` → END

子图 END 时返回当前 partial `NL2SQLState`，`final_sql` / `decision_path` / `error` 任一可读，调用方据此判成败。语义与现有 `run_single_query` 的早退 return 完全一致。

**备选**：用节点内 `raise` + 全局兜底。否决——异常控制流不如条件边可读，且会污染 trace。

### 决策 3：cache 命中短路下沉到子图入口条件边
`single_query_graph` 入口条件边：`cache_hit==True` → `execution`（跳过 ir/ss/cg），否则 → `ir`。主图因此可删除 `route_after_cache`，主图入口直接 `history_cache → task_planner → (single_query | run_subqueries | END)`，cache 命中与否都在 `single_query` 节点内自洽。

**备选**：主图保留 cache 分支绕过 single_query。否决——又制造一条短路特例，违背单一事实来源目标。

### 决策 4：orchestrator 构造签名由「6 个 Agent 实例」改为「1 个编译图」
`SubqueryOrchestrator.__init__(single_query_graph, ...)`。构造方（`deps.py`）先 `build_single_query_graph(...)`，再传给主图与 orchestrator 共用同一编译实例。

**备选**：orchestrator 内部自行 build。否决——主图与 orchestrator 应共用同一编译产物，避免重复编译；且构造方注入便于测试 mock。

### 决策 5：主图 run_single_query 节点 = 单次 invoke
主图新增 `run_single_query` 节点，body 为 `single_query_graph.invoke(state)`，返回子图 partial state（含 `final_sql` / `decision_path` / `rejection_reason` 等）。主图不再逐节点 `add_edge(ir, ss)`。

### 决策 6：ContextVar 与 SSE 装饰透传不变
`_wrap_node` 的 stage started/done 事件、`current_node` ContextVar 仍包裹每个节点。子图 invoke 在同线程内执行，`current_fix_loop` 等对子图节点天然可见，无需额外传递。唯一变化是 SSE 节点名层级从「主图节点」变为「子图节点」，但 event_type/payload 不变，前端无感。

## Risks / Trade-offs

- **[主图 trace 层级变化]** 主图层级不再可见 ir/ss/cg 单独节点（它们下沉到子图）。
  → 缓解：`_wrap_node` 仍发 stage 事件，LangSmith run_name 标注 `single-query` 子图；orchestrator 每个子查询 invoke 前打 `[Orchestrator] 子查询 i/N` 日志保持可串联。
- **[partial state 容错]** orchestrator 从 partial `NL2SQLState` 读 `final_sql` 等字段，cache 命中短路或 fail-fast 早退时部分字段可能缺失。
  → 缓解：orchestrator 用 `.get(field, default)` 容错读取，与现有 `run_single_query` return 默认值语义一致；新增测试覆盖三条早退路径。
- **[构造链 Breaking]** `SubqueryOrchestrator.__init__` 签名变更是内部 breaking。
  → 缓解：所有构造方集中在 `deps.py` 与测试，一次性同步更新；离线脚本搜一遍。
- **[cache 命中行为回归]** cache 命中路径从「主图直接 execution」改为「子图入口条件边 execution」，需验证 `cached_sql` 仍正确构造候选并执行。
  → 缓解：保留 `make_execution_node` 内 cache_hit 分支逻辑不变，新增 cache 命中回归测试。

## Migration Plan

1. 新增 `src/graph/single_query_graph.py`，平移主图 6 节点 + 条件边，编译。
2. 改 `SubqueryOrchestrator`：删 `run_single_query()`，`__init__` 收图，`run()` for 循环体改 invoke。
3. 改主图：删 ir/ss/answerability/cg/execution/decision 节点及边，新增 `run_single_query` 节点；删 `route_after_cache`、简化 `route_after_planner`。
4. 改 `deps.py`：`build_single_query_graph(...)` 后注入主图与 orchestrator。
5. 改测试：`tests/clarification` 调 `run_single_query()` 处改 invoke 图；补 cache 命中 / 三条 fail-fast 回归测试。
6. 全量跑现有测试确认行为不变。

**回滚**：单一 commit，回滚即恢复主图节点链与 `run_single_query()`。无数据/配置迁移。

## Open Questions

- 无。串行、ContextVar、SSE 契约均已约束明确，可直接开发。

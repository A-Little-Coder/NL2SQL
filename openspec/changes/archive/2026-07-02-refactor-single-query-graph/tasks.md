## 1. 新建 single_query_graph

- [x] 1.1 新建 `src/graph/single_query_graph.py`，定义 `build_single_query_graph(retriever, selector, generator, fix_loop, decider, answerability_checker=None)` 工厂
- [x] 1.2 平移主图 6 节点：用 `make_ir_node` / `make_ss_node` / `make_answerability_check_node`（可选）/ `make_cg_node` / `make_execution_node` / `make_decision_node` 注册到 `StateGraph(NL2SQLState)`，复用 `_wrap_node` 装饰
- [x] 1.3 实现入口条件边：`cache_hit==True` → `execution`，否则 → `ir`
- [x] 1.4 实现 fail-fast 条件边：`ss` 无 schema → END；`answerability_check` `answerable=="false"` → END；`cg` 无候选 → END
- [x] 1.5 固定边：`ir→ss`、`execution→decision`、`decision→END`；`answerability_check→cg`（启用时）/ `ss→cg`（未启用时）
- [x] 1.6 编译并 `with_config(run_name="single-query")`

## 2. 重构 SubqueryOrchestrator

- [x] 2.1 `__init__` 签名改为接收已编译的 `single_query_graph`（移除 6 个 Agent 实例参数）
- [x] 2.2 删除 `run_single_query()` 函数及其顺序 invoke 胶水
- [x] 2.3 `run()` for 循环体改为 `single_query_graph.invoke({**shared_state, "user_query": subq})`，从返回 partial state 用 `.get()` 容错读取 `final_sql`/`final_result`/`decision_path`/`fix_failed`/`error` 组装 `SubqueryResult`
- [x] 2.4 保留失败隔离 try/except（invoke 抛异常记 `decision_path="FAILED"`，不中断后续）

## 3. 重构主图 main_graph.py

- [x] 3.1 删除 ir/ss/answerability_check/cg/execution/decision 六个 `add_node` 及其间的条件边
- [x] 3.2 新增 `run_single_query` 节点：body 为 `single_query_graph.invoke(state)`，返回子图 partial state
- [x] 3.3 保留 `route_after_cache` 但目标改为 `run_single_query`（cache 命中跳过 task_planner，短路逻辑在子图入口直奔 execution；比"删 route_after_cache"更优，避免 cache 命中白跑一次 LLM 裁决）
- [x] 3.4 简化 `route_after_planner`：reject→END，multi→`run_subqueries`，其余→`run_single_query`
- [x] 3.5 `build_main_graph` 入参新增 `single_query_graph`（由调用方编译注入），原 retriever/selector/generator/fix_loop/decider 等仍保留（single_query_graph 构造用，且向后兼容）
- [x] 3.6 调整 `run_subqueries` 节点：orchestrator 已持有图，`make_run_subqueries_node` 适配新签名

## 4. 更新构造方

- [x] 4.1 `src/api/deps.py`：先 `build_single_query_graph(...)`，同一编译实例注入主图 `build_main_graph` 与 `SubqueryOrchestrator`
- [x] 4.2 搜全仓离线脚本 / CLI 调用方，同步 `SubqueryOrchestrator` 与 `build_main_graph` 新签名

## 5. 测试

- [x] 5.1 `tests/clarification/` 下直接调 `run_single_query()` 的用例改为 invoke `single_query_graph`
- [x] 5.2 新增 `single_query_graph` 单测：正常单意图、cache 命中短路、cache 命中但 cached_sql 空
- [x] 5.3 新增 fail-fast 回归：SS 无 schema、不可回答、CG 无候选 三条早退路径
- [x] 5.4 多意图 orchestrator 回归测试：N 子查询串行、单子查询失败隔离、partial state 容错读取
- [x] 5.5 SSE 事件契约测试：重构后单/多意图查询的 event_type 与 payload 结构不变
- [x] 5.6 全量跑 `tests/` 确认无回归（`pytest -q`）

## 6. 验收

- [x] 6.1 `run_single_query()` 函数已从 orchestrator 删除，单查询胶水仅存于 `single_query_graph` 一处
- [x] 6.2 主图节点数为 5（history_cache / task_planner / run_single_query / run_subqueries / aggregate_results / memory_update），ir/ss/cg/execution/decision 已下沉子图
- [x] 6.3 不变项核对：串行执行、ContextVar 传递、SSE 契约、Agent build_graph() 签名、history_cache/memory_update/aggregate_results 行为均未变

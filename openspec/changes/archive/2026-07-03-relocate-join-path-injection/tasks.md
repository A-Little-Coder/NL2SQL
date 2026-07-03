# Tasks: Relocate JOIN Path Injection to SQL Generation Stage

> 实施顺序遵循依赖关系：先建纯函数（2）→ 再建节点工厂与 state 字段（3、4）→ 再接图（1）→
> 再接消费端（5、6）→ 最后清理 IR 死代码（7）→ 测试（8）→ 跨 change 协调（9）。

## 1. 流水线编排：插入 schema_finalize 节点
- [x] 1.1 在 `src/graph/single_query_graph.py` 新增 `schema_finalize` 节点（`_wrap_node` 装饰）
- [x] 1.2 `ss` 出口条件边由 `→ answerability_check/cg` 改为 `→ schema_finalize`
- [x] 1.3 `schema_finalize → answerability_check`（启用时）或 `→ cg`（未启用时），保留原
      `route_after_ss` 的「无 schema → END」fail-fast 语义上移到 ss 出口或 schema_finalize 入口
- [x] 1.4 确认 cache_hit 短路（START → execution）不受影响

## 2. JOIN 逻辑下沉为纯函数
- [x] 2.1 在 `src/preprocessing/schema_graph_builder.py` 新增 `enrich_schema_with_join_paths()`
      纯函数（签名见 design §2.3），复用现有 `extract_join_paths` / `format_join_paths_for_prompt`
- [x] 2.2 实现桥接表 M-Schema 补全：用 `vector_store` + `vectorizer` 查桥接表列，转 `MSchemaTable`
      补进 `selected_schema`（metadata 字段映射复用 `to_mschema` 同款逻辑）
- [x] 2.3 降级分支：`database_filter` 空 / 表数 < 2 / 图不存在 / 无 edges / 异常 →
      `selected_schema` 原样返回，`join_paths_text=""`
- [x] 2.4 迁移 `tests/preprocessing/test_schema_graph_builder.py::test_add_bridge_tables` 为
      `enrich_schema_with_join_paths` 单测（断言桥接表被补成 MSchemaTable、join_paths_text 非空）

## 3. 节点工厂
- [x] 3.1 在 `src/graph/main_graph.py` 新增 `make_schema_finalize_node(retriever)` 工厂
- [x] 3.2 节点内：取 `state["selected_schema"]` + `state["database_filter"]`，调
      `enrich_schema_with_join_paths`（vector_store/vectorizer/data_dir 从 retriever 或注入获取），
      回写 `selected_schema`（含桥接表）与 `join_paths_text`
- [x] 3.3 节点内 try/except 兜底：异常时 `join_paths_text=""`、schema 原样放行，记 trace_log
- [x] 3.4 节点级业务摘要日志（带 qid）：`[SchemaFinalize] join_edges=N bridge_tables=M`
- [x] 3.5 SSE 事件（可选）：`emit_safe("schema_finalize", {...})`

## 4. State 字段
- [x] 4.1 `src/graph/state.py` 的 `NL2SQLState` 新增 `join_paths_text: str` 字段
- [x] 4.2 `create_initial_state()` 补 `join_paths_text=""` 默认值
- [x] 4.3 更新 docstring

## 5. CG 消费 join_paths_text
- [x] 5.1 `src/sql_generation/cg_graph.py` 的 `node_llm_generate_and_validate` 从 state 取
      `join_paths_text`（需经主图 CG 节点透传进子图 invoke 入参）
- [x] 5.2 非空时追加 `HumanMessage("## 表关联\n{join_paths_text}")`（沿用现有追加模式）
- [x] 5.3 `make_cg_node` 子图 invoke 入参加 `join_paths_text: state.get("join_paths_text", "")`

## 6. Execution / SmartFix 消费 join_paths_text
- [x] 6.1 `make_execution_node` 生成 `schema_text` 时，若 `join_paths_text` 非空则拼接
      `"\n\n## 表关联\n" + join_paths_text`
- [x] 6.2 验证 `schema_text` 经 Decision 子图流入 SmartFix 修复 Prompt（decision_graph 已用
      `schema_text`，无需额外改动）

## 7. 清理 IR 死代码
- [x] 7.1 删除 `src/retrieval/information_retrieval.py` 的 `_inject_join_paths()` 方法
- [x] 7.2 删除 `_add_bridge_tables()` 方法
- [x] 7.3 删除 `retrieve()` 第 7 步 `context = self._inject_join_paths(...)` 调用
- [x] 7.4 删除 `RetrievedContext.join_paths` / `join_paths_text` 字段及 `__post_init__` 初始化
- [x] 7.5 全仓库 grep 确认无残留引用（`join_paths` / `_inject_join_paths` / `_add_bridge_tables`）

## 8. 测试与回归
- [x] 8.1 新增 `tests/graph/test_subgraphs.py`（或等价）断言 `single_query_graph` 节点链含
      `schema_finalize`，且位于 ss 与 answerability_check/cg 之间
- [x] 8.2 新增多表查询端到端用例：断言 `join_paths_text` 非空、CG Prompt 含表关联、桥接表
      M-Schema 进入 selected_schema
- [x] 8.3 单表查询用例：`join_paths_text=""`，schema 不变
- [x] 8.4 cache_hit 短路用例：不触发 schema_finalize
- [x] 8.5 `database_filter=None` 降级用例：`join_paths_text=""`，不抛异常
- [x] 8.6 运行现有回归套件，确保无破坏

## 9. 跨 change 协调
- [x] 9.1 从 `nl2sql-agent-system/specs/information-retrieval.md` 移除「JOIN 路径注入在 IR」相关
      Scenario
- [x] 9.2 在 `nl2sql-agent-system/specs/sql-generation.md` 补充 CG Prompt 注入 join_paths_text 说明
      （或注明由 `schema-relationship-graph` spec 覆盖）
- [x] 9.3 在 `nl2sql-agent-system/design.md` 决策 26 处标注「实现位置已由 relocate-join-path-injection
      变更，JOIN 注入迁移至 SS→CG 之间」
- [x] 9.4 归档顺序确认：若 nl2sql-agent-system 先归档，须先完成 9.1~9.3

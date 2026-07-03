## MODIFIED Requirements

### Requirement: Single Query Pipeline Graph
系统 SHALL 提供一个编译好的 `single_query_graph`，以 `NL2SQLState` 为输入与输出 schema，依次执行 ir → ss → schema_finalize → answerability_check（若启用）→ cg → execution → decision 七个阶段，作为单意图路径、history_cache 命中路径与多意图串行编排三处共用的单一事实来源。`schema_finalize` 节点 SHALL 位于 `ss` 之后、`answerability_check`（或未启用时的 `cg`）之前，负责基于收窄后的 `selected_schema` 计算表间 JOIN 路径、补充桥接表 M-Schema、产出 `join_paths_text`。

#### Scenario: 正常单意图查询
- **WHEN** 输入 `NL2SQLState` 的 `cache_hit` 为 False 且各阶段均成功产出
- **THEN** 图依次经过 ir → ss → schema_finalize → answerability_check → cg → execution → decision，最终在 `final_sql` 写入选定 SQL、`final_result` 写入执行结果、`decision_path` 写入决策路径标识

#### Scenario: 复用现有节点工厂
- **WHEN** 编译 `single_query_graph`
- **THEN** 图节点 SHALL 复用 `make_ir_node` / `make_ss_node` / `make_schema_finalize_node` / `make_answerability_check_node` / `make_cg_node` / `make_execution_node` / `make_decision_node` 节点工厂，适配逻辑与主图原实现一致，不重复实现 Agent 子图调用胶水

#### Scenario: ss 出口路由经 schema_finalize
- **WHEN** `ss` 阶段产出非空 `selected_schema`
- **THEN** 图 SHALL 路由至 `schema_finalize` 节点（而非直接进 answerability_check / cg），由其完成 JOIN 路径注入与桥接表补全后再放行

#### Scenario: ss 未选出 schema 仍 fail-fast
- **WHEN** `ss` 阶段未产出 `selected_schema`（空列表）
- **THEN** 图经条件边直接 END，不进入 schema_finalize / answerability_check / cg / execution / decision，返回的 state 中 `selected_schema` 为空、`join_paths_text` 为空

## ADDED Requirements

### Requirement: Schema Finalization Node
`schema_finalize` 节点 SHALL 以 SS 产出的 `selected_schema`（`List[MSchemaTable]`）与 `database_filter` 为输入，调用 `schema_graph_builder.enrich_schema_with_join_paths()` 计算选中表之间的 JOIN 路径，把桥接表 M-Schema 补入 `selected_schema`，并把格式化后的 `join_paths_text` 写入 `NL2SQLState`。节点 SHALL 在桥接表补全完成后才放行至 `answerability_check`，使可回答性判断能看到桥接表存在。

#### Scenario: 多表查询产出 join_paths_text
- **WHEN** `selected_schema` 含 2 张及以上表且 `database_filter` 指定了已构建关联图的数据库
- **THEN** 节点 SHALL 调用 `enrich_schema_with_join_paths` 计算选中表之间的最短 JOIN 路径
- **AND** 节点 SHALL 把 `format_join_paths_for_prompt` 的输出写入 state 的 `join_paths_text`
- **AND** 节点 SHALL 把路径上的桥接表转为 `MSchemaTable` 补入 `selected_schema` 并回写 state

#### Scenario: 桥接表补全先于可回答性检查
- **WHEN** JOIN 路径识别出桥接表（路径中出现但未被 SS 选中的表）
- **THEN** 节点 SHALL 在放行至 `answerability_check` 之前完成桥接表 M-Schema 补全
- **AND** 使 answerability_check 能据含桥接表的 `selected_schema` 判断表间可连接性

#### Scenario: 单表或无 database_filter 降级
- **WHEN** `selected_schema` 表数 < 2，或 `database_filter` 为空，或关联图文件不存在，或未提取到任何 JOIN 边
- **THEN** 节点 SHALL 保持 `selected_schema` 不变
- **AND** 节点 SHALL 将 `join_paths_text` 置为空字符串
- **AND** 节点 SHALL 正常放行，不阻断流水线

#### Scenario: 节点异常兜底
- **WHEN** `enrich_schema_with_join_paths` 抛出异常
- **THEN** 节点 SHALL 捕获异常，保持 `selected_schema` 原样、`join_paths_text` 置空
- **AND** 节点 SHALL 记录 trace_log 并正常放行（降级为无 JOIN 提示）

#### Scenario: cache_hit 短路不触发
- **WHEN** 输入 state 的 `cache_hit` 为 True
- **THEN** 图入口条件边路由至 `execution`，不执行 ir / ss / schema_finalize / cg

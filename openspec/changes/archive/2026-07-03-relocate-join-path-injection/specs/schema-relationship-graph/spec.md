## ADDED Requirements

> 表关联（JOIN）路径计算与注入能力。定义纯函数 `enrich_schema_with_join_paths()`，以 SS 收窄后的
> `selected_schema`（`List[MSchemaTable]`）为输入，基于预构建的表关联图
> （`schema_graphs/{db_id}.json`）计算选中表之间的最短 JOIN 路径，补充桥接表 M-Schema，产出
> `join_paths_text` 供 CG 生成 Prompt 与 SmartFix 修复 Prompt 双消费。该能力取代 IR 阶段的 JOIN
> 注入实现（决策 26），将注入时机后移到 schema 收窄之后、SQL 生成之前。

### Requirement: JOIN 路径计算基于收窄后的 schema
`enrich_schema_with_join_paths()` SHALL 以 `selected_schema`（SS 产物，非 IR 召回的全量表集合）中的
表名集合为输入，调用 `extract_join_paths(graph, table_names)` 做最短路径 BFS 提取，仅在真正参与查询的
表之间计算 JOIN 关系。

#### Scenario: 输入为 SS 产物而非 IR 全量召回
- **WHEN** 调用 `enrich_schema_with_join_paths`
- **THEN** 它 SHALL 从 `selected_schema`（`List[MSchemaTable]`）提取表名集合作为 JOIN 路径计算的输入
- **AND** 它 SHALL NOT 使用 IR 召回的 `RetrievedContext` 全量表集合

#### Scenario: 返回补全后的 schema 与 join_paths_text
- **WHEN** 计算完成
- **THEN** 函数 SHALL 返回二元组 `(补全桥接表后的 selected_schema, join_paths_text)`
- **AND** 返回的 `selected_schema` 元素类型 SHALL 仍为 `MSchemaTable`

### Requirement: 桥接表 M-Schema 补全
当 JOIN 路径中出现未被 `selected_schema` 包含的桥接表时，`enrich_schema_with_join_paths()` SHALL 从
向量库（ChromaDB `nl2sql_columns` collection）查询桥接表的列，转换为 `MSchemaTable` 补入
`selected_schema`，使 LLM 能写出经桥接表的 JOIN。

#### Scenario: 桥接表转换为 MSchemaTable
- **WHEN** `extract_join_paths` 返回的 `bridge_tables` 非空
- **THEN** 函数 SHALL 对每个桥接表，用 `vector_store` 查询其列（where `database` + `table_name`）
- **AND** 函数 SHALL 把查询返回的列元数据（`data_type` / `description` / `sample_values` /
  `is_primary_key` / `references` / `original_column_name`）转为 `MSchemaColumn`，组装成 `MSchemaTable`
- **AND** 函数 SHALL 把该 `MSchemaTable` 追加进 `selected_schema`（去重，不重复添加已存在的表）

#### Scenario: 已存在的表不重复补全
- **WHEN** 桥接表名已在 `selected_schema` 中
- **THEN** 函数 SHALL 跳过该表，不重复添加

### Requirement: 运行时依赖参数注入
`enrich_schema_with_join_paths()` SHALL 为纯函数，运行时依赖（`vector_store`、`vectorizer`、`data_dir`）
通过参数注入，SHALL NOT 在函数内部硬实例化 ChromaDB 或向量器，保持 `schema_graph_builder` 模块的纯工具定位。

#### Scenario: 函数签名
- **WHEN** 调用 `enrich_schema_with_join_paths`
- **THEN** 其签名 SHALL 为 `enrich_schema_with_join_paths(selected_schema, database_filter, vector_store, vectorizer, data_dir) -> tuple[List[MSchemaTable], str]`

### Requirement: 降级与边界处理
`enrich_schema_with_join_paths()` SHALL 在以下情形优雅降级，返回 `selected_schema` 原样、`join_paths_text=""`，
不抛异常：`database_filter` 为空、表数 < 2、关联图文件不存在、未提取到 JOIN 边。

#### Scenario: database_filter 为空
- **WHEN** `database_filter` 为 None 或空字符串
- **THEN** 函数 SHALL 返回 `selected_schema` 原样、`join_paths_text=""`

#### Scenario: 表数不足
- **WHEN** `selected_schema` 表数 < 2
- **THEN** 函数 SHALL 返回 `selected_schema` 原样、`join_paths_text=""`

#### Scenario: 关联图不存在或无边
- **WHEN** `schema_graphs/{db_id}.json` 不存在，或 `extract_join_paths` 返回空 edges
- **THEN** 函数 SHALL 返回 `selected_schema` 原样、`join_paths_text=""`

### Requirement: join_paths_text 双消费
`join_paths_text` SHALL 被 CG 生成 Prompt 与 SmartFix 修复 Prompt 两处消费：CG 子图生成 SQL 时将其
作为追加 `HumanMessage` 注入；Execution 节点生成 `schema_text` 时将其拼接，使经 `schema_text` 通道的
SmartFix 修复 Prompt 也能看到表关联。

#### Scenario: CG 生成 Prompt 消费
- **WHEN** `NL2SQLState.join_paths_text` 非空且 CG 子图生成 SQL 候选
- **THEN** CG 子图 SHALL 追加一条含 `join_paths_text` 的 `HumanMessage`（沿用 preference / metric /
  historical 的追加模式）

#### Scenario: SmartFix 修复 Prompt 消费
- **WHEN** `join_paths_text` 非空且 Execution 节点生成 `schema_text`
- **THEN** Execution 节点 SHALL 将 `join_paths_text` 拼接到 `schema_text`（如 `"\n\n## 表关联\n" + join_paths_text`）
- **AND** 该 `schema_text` SHALL 经 Decision 子图流入 SmartFix 修复 Prompt

### Requirement: IR 阶段不再注入 JOIN
`InformationRetrieval` SHALL NOT 在 `retrieve()` 中计算或注入 JOIN 路径；`RetrievedContext` SHALL NOT
携带 `join_paths` / `join_paths_text` 字段。JOIN 注入职责完全转移至 `schema_finalize` 节点。

#### Scenario: IR 不再产出 join_paths
- **WHEN** `InformationRetrieval.retrieve()` 执行
- **THEN** 它 SHALL NOT 调用任何 JOIN 路径计算逻辑
- **AND** 返回的 `RetrievedContext` SHALL 不含 `join_paths` / `join_paths_text` 字段

#### Scenario: 删除 IR 相关方法与字段
- **WHEN** 本变更实施完成
- **THEN** `InformationRetrieval._inject_join_paths()` / `_add_bridge_tables()` 方法 SHALL 被删除
- **AND** `RetrievedContext.join_paths` / `join_paths_text` 字段及其初始化 SHALL 被删除

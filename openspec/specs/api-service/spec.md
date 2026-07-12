## MODIFIED Requirements

### Requirement: schema_recall 事件携带关键词组聚合的完整召回数据

`schema_recall` SSE 事件 SHALL 以 `keyword_groups` 为顶层字段，按关键词组聚合展示该组的同义词、召回字段与召回值。每个 keyword_group 含 `phrase`、`terms`、`columns`（每项含 table/column/score）、`values`（每项含 value/table/column/score，按后端 `source_phrase` 归属）。系统 MUST NOT 再发送空的 `groups` 字段。

#### Scenario: schema_recall 含按组聚合的字段与值
- **WHEN** IR 节点完成检索并 emit `schema_recall` 事件
- **THEN** 事件 payload 含 `keyword_groups` 数组，每个元素含 `phrase`、`terms`、`columns`、`values`
- **AND** `columns` 每项含 `table`/`column`/`score`，来自 `RetrievedContext.keyword_columns_map` + `columns` 详情
- **AND** `values` 每项含 `value`/`table`/`column`/`score`，按 `metadata.source_phrase` 过滤归属到本组

#### Scenario: 无召回数据的关键词组
- **WHEN** 某关键词组未召回任何字段或值
- **THEN** 该组仍出现在 `keyword_groups` 中，`columns`/`values` 为空数组
- **AND** 前端据空数组展示"无召回"占位

### Requirement: 反问 interrupt 不触发 error 事件

当图节点调用 `interrupt()` 挂起等待用户反问回答时，系统 MUST NOT emit `error` 事件。`GraphInterrupt` 是 langgraph 的正常控制流信号，节点装饰器 `_wrap_node` SHALL 放行该异常（直接 re-raise 不 emit error），仅由 `query.py` 检测 `__interrupt__` 后 emit `clarification` 事件。

#### Scenario: 反问仅产生 clarification 事件
- **WHEN** `cache_confirm` 或 `task_planner` 节点调用 `interrupt()` 触发反问
- **THEN** SSE 流仅 emit `clarification` 事件（含 question/ambiguities/round/awaiting_answer）
- **AND** 不 emit 任何 `error` 事件
- **AND** 前端时间轴不出现红色错误节点，仅停在反问节点

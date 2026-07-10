## ADDED Requirements

### Requirement: Column Document Format
`SchemaColumnDocGenerator.format_column_document` SHALL 产出格式为 `{table_name} | {original_column_name} | {desc}` 的文档文本：以 ` | ` 连接非空部分，整体 `.lower()`。`desc` SHALL 按优先级 `column_description -> value_description -> column_name -> original_column_name` 取首个非空值。该格式 SHALL NOT 在末尾重复 `column_name` 做 boost。

#### Scenario: 有列描述时 desc 取列描述
- **WHEN** 调用 `format_column_document(table_name="orders", original_column_name="total_amt", column_name="订单总额", column_description="订单总金额，包含运费")`
- **THEN** 产出 SHALL 为 `"orders | total_amt | 订单总金额，包含运费"`（`desc` 取 `column_description`，`column_name` 不单独出现）

#### Scenario: 无列描述时 desc 回退列名
- **WHEN** 调用 `format_column_document(table_name="t", original_column_name="x", column_name="销量")`
- **THEN** 产出 SHALL 为 `"t | x | 销量"`（`desc` 回退 `column_name`，无末尾 boost）

#### Scenario: 分隔符为管道符
- **WHEN** 任一调用产出文档
- **THEN** 各非空部分 SHALL 以 ` | ` 连接（非空格），整体 lower

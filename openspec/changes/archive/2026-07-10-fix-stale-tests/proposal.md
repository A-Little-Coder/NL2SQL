## Why

全量 pytest 有 12 个既有失败，与 `harden-history-cache` 无关，阻塞干净测试基线：

1. **test_db_pool（9 个）**：`Globals` 数据类在 memory 迁移（commit b26b6c1）后新增 `memory_dir` 必填字段（db_pool.py:73），但 `tests/api/test_db_pool.py::_make_pool` 构造 `Globals` 时未传，`TypeError: missing 'memory_dir'`。
2. **test_schema_doc_generator（3 个）**：`schema_doc_generator.format_column_document` 在 edf91b7（IR 召回优化）中改为 ` | ` 分隔、去掉末尾 boost，但测试与模块 docstring（line 21 仍提 boost）未同步，断言期望空格分隔 + boost。

两处均为「实现已改、测试/docstring 滞后」，非行为回归。

## What Changes

- 修 `tests/api/test_db_pool.py::_make_pool`：`Globals` 构造补 `memory_dir="/fake/memory"`；全量检查该文件其他 `Globals` 构造点。
- 修 `tests/preprocessing/test_schema_doc_generator.py`：3 个断言改为匹配现格式 `{table} | {original} | {desc}`（desc 优先级 `column_description -> value_description -> column_name`，无末尾 boost）。
- 修 `src/preprocessing/schema_doc_generator.py` 模块 docstring（line 21）：去掉过时 boost 描述，与 `format_column_document` 实现一致。
- 补 `schema-doc-generation` 能力 spec：将此前未入 spec 的列文档格式行为写入 spec，消除后续测试漂移。

## Capabilities

### New Capabilities
- `schema-doc-generation`: 列文档生成格式（` | ` 分隔、desc 优先级、无 boost）--此前未入 spec，本次补上。

### Modified Capabilities
（无 -- test_db_pool 的 memory_dir 是 memory 迁移的正确产物，本 change 仅修测试 fixture，不改 Globals 契约。）

## Impact

- `tests/api/test_db_pool.py`：`_make_pool` 及其他 `Globals` 构造点补 `memory_dir`。
- `tests/preprocessing/test_schema_doc_generator.py`：3 个断言更新。
- `src/preprocessing/schema_doc_generator.py`：模块 docstring line 21 去过时 boost 描述（无运行时行为变更）。
- 无运行时行为变更，无迁移。

## Open Questions

- **boost 是否应恢复**：`format_column_document` 不再做末尾 boost。本 change 按「代码为权威」处理（改测试 + docstring）。若后续发现 IR 召回因缺 boost 下降，另开 change 恢复。

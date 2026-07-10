## Context

两处既有测试失败均源于「实现演进、测试/docstring 滞后」：

- `Globals`（db_pool.py:61）在 memory 迁移后新增必填 `memory_dir`（db_pool.py:73），`test_db_pool._make_pool` 漏传。
- `format_column_document`（schema_doc_generator.py:28-59）在 edf91b7 改为 ` | ` 分隔、desc 优先级取值、去 boost；模块 docstring（line 21）与测试仍描述旧格式。

## Goals / Non-Goals

**Goals:**
- 12 个既有失败归零，恢复干净 pytest 基线。
- 将列文档格式行为补入 spec，防止再次漂移。

**Non-Goals:**
- 不恢复 boost（除非另议，见 Open Questions）。
- 不动 `Globals` 数据类（`memory_dir` 是 memory 迁移的正确产物）。
- 不动 `format_column_document` 实现（` | ` 格式是 edf91b7 的有意改动）。

## Decisions

### D1：代码为权威，改测试 + docstring，不回退实现
`schema_doc_generator.format_column_document` 的 ` | ` 分隔 + 去 boost 是 edf91b7（IR 召回优化）的有意改动，实现与 line 39 注释一致。故改测试断言与 line 21 docstring 匹配实现，不回退实现。

**备选**：恢复 boost。否决--无证据表明 boost 移除是 bug，且回退可能影响 IR 召回效果。

### D2：test_db_pool 补 memory_dir fixture
`Globals.memory_dir` 是必填字段（db_pool.py:73）。`_make_pool` 构造时补 `memory_dir="/fake/memory"`（fake 路径，测试不真实构建 ctx）。全量检查该文件其他 `Globals` 构造点。

### D3：补 schema-doc-generation spec
列文档格式此前未入 spec，是测试漂移的根因。本次 ADD `schema-doc-generation` 能力，明确格式契约（` | ` 分隔、desc 优先级、无 boost），作为测试断言的依据。

## Risks / Trade-offs

- **[boost 移除实为 bug 的风险]** -> 若后续 IR 召回因缺 boost 下降，另开 change 恢复；本次按现状。
- **[test_db_pool 其他 fixture 也漏 memory_dir]** -> 实现时全量 grep `Globals(` 构造点，逐一补。
- **[docstring 与实现其他不一致]** -> 实现时复核 schema_doc_generator 模块 docstring 全文，确保与实现一致。

## Migration Plan

纯测试 + docstring 修正，无运行时行为变更，无迁移步骤。

## Open Questions

- **boost 是否应恢复**（同 proposal）：本期按现状不恢复。

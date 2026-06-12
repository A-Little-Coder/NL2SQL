# Execution Engine Specification

## Purpose

执行引擎负责对 CG 模块生成的候选 SQL 进行一次性执行，把结果回填到候选对象中，
不在执行阶段触发任何 LLM 修复逻辑。修复职责由 Decision 节点的 SmartFix 子流程承担
（见 `smart-fix-loop` capability）。

## Requirements

### Requirement: 候选 SQL 一次性执行

ExecuteAll 节点 SHALL 对来自 CG 模块的 5 个候选 SQL 进行一次性执行，SHALL NOT 在执行阶段触发任何 LLM 修复调用。所有修复逻辑被移至 Decision 节点的 SmartFix 子流程。

#### Scenario: 候选 SQL 一次性执行不触发修复
- **GIVEN** 来自 CG 模块的 5 个候选 SQL
- **WHEN** ExecuteAll 节点执行时
- **THEN** 它应该对每个候选独立执行（不经过修复循环）
- **AND** 它应该在执行后回填 `result` / `execution_time` / `status` / `error_message` 字段
- **AND** 它应该不在执行阶段触发任何 LLM 修复调用
- **AND** 它应该为每个候选发送一次 `execution` SSE 事件（含 candidate_id / success / rows / error）

#### Scenario: 候选执行失败时记录错误但不立即修复
- **GIVEN** 某个候选 SQL 在 ExecuteAll 中执行失败
- **WHEN** 节点处理该候选时
- **THEN** 它应该将 `status` 设为 `FAILED`
- **AND** 它应该将 `error_message` 设为原始错误信息
- **AND** 它应该将错误的 `StructuredError` 对象保留供后续 SmartFix 使用
- **AND** 它应该继续执行剩余候选（不中断流程）

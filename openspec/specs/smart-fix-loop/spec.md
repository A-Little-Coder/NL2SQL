# Smart Fix Loop Specification

## Purpose

SmartFix 子流程对 Decision 节点选出的单个候选 SQL 执行最多 3 轮修复，
每轮带 fix_history 上下文避免 LLM 反复犯同样的错；
不可修复错误类型（TIMEOUT / RUNTIME / PERMISSION）直接跳过不调 LLM；
每轮通过 SSE `smart_fix_round` 事件推送进度。

## Requirements

### Requirement: SmartFix 单候选最多 3 轮修复

SmartFix 子流程 SHALL 对 Decision 节点选出的单个候选 SQL 执行最多 3 轮修复，任一轮成功立即返回，3 轮全失败则返回标记。

#### Scenario: 最多 3 轮修复与成功早退
- **GIVEN** Decision 节点选出 1 个候选 SQL 需要修复
- **WHEN** SmartFix 子流程启动时
- **THEN** 它应该最多执行 3 轮修复
- **AND** 每轮执行步骤为：(LLM 修复 → 执行新 SQL → 判断是否成功)
- **AND** 当某轮修复后执行成功时它应该立即返回成功的 SQL 和结果
- **AND** 当成功时它应该记录 `fix_rounds_used` 为实际使用的轮次

#### Scenario: 3 轮全失败处理
- **GIVEN** SmartFix 已执行 3 轮
- **WHEN** 第 3 轮仍然失败
- **THEN** 它应该返回 `fix_failed=True`
- **AND** 它应该返回最初的候选 SQL（未修复版本）和最后一轮的错误信息
- **AND** 它应该将 `last_error` 设为最后一次执行的错误信息

### Requirement: SmartFix 每轮带 fix_history 上下文

SmartFix 第 N 轮（N >= 2）的修复 prompt SHALL 包含所有前 N-1 轮的修复记录，避免 LLM 反复犯同样的错。

#### Scenario: fix_history 在 prompt 中传递
- **GIVEN** SmartFix 进入第 N 轮（N >= 2）
- **WHEN** 构建 LLM 修复 prompt 时
- **THEN** 它应该包含所有前 N-1 轮的修复记录
- **AND** 每条记录应包含 `{round, sql, error}` 三个字段
- **AND** prompt 应明确指示"请基于这些历史，避免再犯同样的错"
- **AND** 它应该使 LLM 能识别出反复出现的错误模式

### Requirement: SmartFix 跳过不可修复错误类型

对 TIMEOUT/RUNTIME/PERMISSION 类错误 SmartFix MUST NOT 调用 LLM，SHALL 直接返回失败标记，避免无效 LLM 调用。

#### Scenario: 不可修复错误直接返回
- **GIVEN** SmartFix 接收到候选 SQL 及其错误
- **WHEN** 错误类型为 TIMEOUT_ERROR / RUNTIME_ERROR / PERMISSION_ERROR 之一时
- **THEN** 它应该不调用 LLM 进行修复
- **AND** 它应该立即返回 `fix_failed=True`
- **AND** 它应该将 `fix_rounds_used` 设为 0
- **AND** 它应该将 `last_error` 设为原始错误信息

### Requirement: 全失败分支调用 SmartFix 时不带评分上下文

当 SmartFix 是从全失败分支被调用时，prompt MUST NOT 包含 R1/R2 评分及评价（因为评分阶段未触发）。

#### Scenario: 全失败分支的修复 prompt
- **GIVEN** 全失败分支选出最轻错误候选进入 SmartFix
- **WHEN** 构建修复 prompt 时
- **THEN** 它应该不包含 R1/R2 评分及评价
- **AND** prompt 应仅包含原始 SQL + 错误信息 + schema + fix_history（如果是第 2+ 轮）

### Requirement: 每轮修复后通过 SSE 推送进度

SmartFix 每轮完成后 SHALL 发送 SSE `smart_fix_round` 事件，前端可实时展示修复过程。

#### Scenario: SSE 事件推送
- **GIVEN** SmartFix 完成第 N 轮的执行
- **WHEN** 生成 SSE 事件时
- **THEN** 它应该发送 `smart_fix_round` 事件
- **AND** 事件应包含 `{round, sql, error, success}` 四个字段
- **AND** 当 success=true 时事件应是该候选的最后一条 smart_fix_round 事件
- **AND** 当 3 轮全失败时第 3 条事件的 success=false

### Requirement: SmartFix 修复后不再评分

修复成功的 SQL SHALL 直接返回，MUST NOT 触发 ScoreByData/ScoreBySQL 二次评估，避免额外 LLM 调用。

#### Scenario: 修复成功后直接返回
- **GIVEN** SmartFix 某轮修复后执行成功
- **WHEN** 处理成功结果时
- **THEN** 它应该直接返回（不调用 ScoreByData / ScoreBySQL 再次评估修复质量）
- **AND** `trace_log` 应记录修复前后的 SQL 差异以便 debug

### Requirement: SmartFix 失败时返回结构化错误信息供 downstream 处理

3 轮修复全失败时 DecisionResult SHALL 保留完整失败信息，使 downstream 能区分"系统救不出来"与"答案不对"。

#### Scenario: 失败返回结构化信息
- **GIVEN** SmartFix 3 轮全部失败
- **WHEN** 返回决策结果时
- **THEN** `DecisionResult.fix_failed` 应为 True
- **AND** `DecisionResult.last_error` 应包含最后一次执行的完整错误信息
- **AND** `DecisionResult.selected_sql` 应为最初候选的 SQL（保持调用方能拿到"尽量好的 SQL"）
- **AND** `DecisionResult.selected_result` 应为 None
- **AND** downstream 可基于 `fix_failed=True` 决定是否向用户展示"系统无法生成可执行 SQL"

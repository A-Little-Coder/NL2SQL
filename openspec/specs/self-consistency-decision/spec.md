# Self-Consistency Decision Specification

## Purpose

Decision 节点对 ExecuteAll 后的多个候选 SQL 进行两段式评分（R1 数据视角 + R2 SQL 视角），
按评分结果路由到 8 条决策路径之一（A-H），选出最优候选；
失败候选通过 SmartFix 子流程（见 `smart-fix-loop` capability）修复；
最终选定的 SQL 进入 result_verifier 做可信度收尾验证。

## Requirements

### Requirement: ScoreByData 第一轮数据视角评分

Decision 节点 SHALL 对所有执行成功的候选进行第一轮 LLM 评分（0-5 分），仅基于结果数据（隐藏 SQL 代码），强制 LLM 从"数据是否答对了"的角度评判。

#### Scenario: 第一轮评分输入构造
- **GIVEN** ExecuteAll 后至少有 1 个候选执行成功
- **WHEN** Decision 节点触发 ScoreByData 评分时
- **THEN** 它应该剔除所有执行失败的候选
- **AND** 它应该为每个成功候选构建数据视角的评分输入（用户 query + 列名 + top-20 行 + 行数 + 执行时间）
- **AND** 它应该将每个单元格内容截断到 20 字符以内
- **AND** 它应该在 prompt 中明示"结果数据为 top-20 行节选，请基于此评分但不要因为只看到 20 行而误判数据量"
- **AND** 它应该不在 prompt 中包含 SQL 代码（强制 LLM 仅基于数据评分）

#### Scenario: 第一轮评分调用与输出
- **GIVEN** ScoreByData 的评分输入构造完成
- **WHEN** 调用 LLM 进行评分
- **THEN** 它应该调用 LLM 进行 0-5 分评分
- **AND** 它应该返回 `[{id, score, reason}]` 格式的评分结果
- **AND** 它应该通过 SSE 发送 `score_r1` 事件

### Requirement: ScoreBySQL 第二轮 SQL 视角评分

Decision 节点 SHALL 仅在 R1 出现并列最高分=5 时触发第二轮 LLM 评分，对并列候选基于 SQL 代码视角再评一次，使用严格模式评分标准。

#### Scenario: 第二轮触发条件
- **GIVEN** R1 评分完成
- **WHEN** 最高分=5 且有多个候选并列时
- **THEN** 它应该触发 ScoreBySQL 第二轮评分
- **AND** 当最高分=5 且仅一个候选时它应该跳过 ScoreBySQL 并直接返回该候选
- **AND** 当最高分<5 时它应该跳过 ScoreBySQL 并直接选择最高分候选进入 SmartFix

#### Scenario: 第二轮评分输入与输出
- **GIVEN** R1 并列最高分=5 触发 R2
- **WHEN** Decision 节点触发 ScoreBySQL 评分时
- **THEN** 它应该为每个并列候选构建 SQL 视角的评分输入（用户 query + SQL 代码 + 执行时间 + R1 评分及评价）
- **AND** 它应该使用严格模式评分标准（0-5 分）
- **AND** 它应该调用 LLM 进行评分
- **AND** 它应该返回 `[{id, score, reason}]` 格式的评分结果
- **AND** 它应该通过 SSE 发送 `score_r2` 事件（含 triggered_by 字段）

### Requirement: 决策路径 A 至 E 的路由规则

Decision 节点 SHALL 根据 R1/R2 评分结果路由到 5 条主路径之一（A: R1 唯一=5; B: R1 并列=5 → R2 唯一最高; C: R1 并列=5 → R2 并列; D: R1<5 → SmartFix 成功; E: R1<5 → SmartFix 失败）。

#### Scenario: 路径 A——R1 唯一最高=5 直接返回
- **GIVEN** R1 评分后存在唯一最高分=5 的候选
- **WHEN** 决策路由判定时
- **THEN** 它应该直接选择该候选作为最终结果
- **AND** 它应该跳过 R2 和 SmartFix
- **AND** 它应该将 `decision_path` 设为 "A"
- **AND** 它应该进入 result_verifier 验证

#### Scenario: 路径 B/C——R1 并列最高=5 触发 R2
- **GIVEN** R1 评分后存在多个并列最高分=5 的候选
- **WHEN** 决策路由判定时
- **THEN** 它应该触发 R2 评分
- **AND** 当 R2 评分后存在唯一最高分时它应该选择该候选并将 `decision_path` 设为 "B"
- **AND** 当 R2 评分后仍存在并列最高分时它应该选择并列组中 `execution_time` 最短的候选并将 `decision_path` 设为 "C"
- **AND** B/C 路径均跳过 SmartFix（视为高质量结果直接返回）
- **AND** 进入 result_verifier 验证

#### Scenario: 路径 D/E——R1 最高分<5 进入 SmartFix
- **GIVEN** R1 评分后最高分<5（无论唯一或并列）
- **WHEN** 决策路由判定时
- **THEN** 它应该选择最高分候选（并列时按 `execution_time` 选最快）
- **AND** 它应该将该候选送入 SmartFix
- **AND** 当 SmartFix 成功时它应该将 `decision_path` 设为 "D" 并返回修复后的 SQL 和结果
- **AND** 当 SmartFix 3 轮全部失败时它应该将 `decision_path` 设为 "E"
- **AND** 当路径为 E 时它应该将 `fix_failed` 设为 True
- **AND** 当路径为 E 时它应该将 `last_error` 设为最后一次执行的错误信息
- **AND** 当路径为 E 时它应该返回最佳 SQL（修复前的原始候选）+ 报错信息

### Requirement: 全失败按错误等级逐个修复

当所有候选执行失败时，Decision 节点 SHALL 按错误严重程度排序，对最轻一级的候选逐个尝试 SmartFix，任一成功立即返回，避免无效修复。

#### Scenario: 错误等级排序与最轻候选选取
- **GIVEN** ExecuteAll 后全部 5 个候选都执行失败
- **WHEN** 决策路由进入全失败分支时
- **THEN** 它应该按错误等级（SEMANTIC=1 < SYNTAX=2 < UNKNOWN=3 < TIMEOUT=4 < RUNTIME=5 < PERMISSION=6）排序
- **AND** 它应该取最轻一级的所有候选（可能多个）

#### Scenario: 路径 H——最轻全部不可修复直接返回
- **GIVEN** 全失败分支取到的最轻候选全部为 TIMEOUT/RUNTIME/PERMISSION
- **WHEN** 路由判定时
- **THEN** 它应该直接返回 `fix_failed=True`
- **AND** 它应该将 `decision_path` 设为 "H"
- **AND** 它应该不调用任何 LLM

#### Scenario: 路径 F——逐个修复任一成功立即返回
- **GIVEN** 全失败分支取到的最轻候选包含可修复错误（SEMANTIC/SYNTAX/UNKNOWN）
- **WHEN** 路由按候选顺序逐个调用 SmartFix
- **THEN** 当任一候选 SmartFix 成功时它应该立即返回该候选的修复结果
- **AND** 当任一候选成功时它应该将 `decision_path` 设为 "F"
- **AND** 当任一候选成功时它应该不再尝试剩余候选

#### Scenario: 路径 G——所有最轻候选都修复失败
- **GIVEN** 全失败分支取到的最轻候选都进入了 SmartFix
- **WHEN** 所有候选都 SmartFix 3 轮失败
- **THEN** 它应该返回 `fix_failed=True`
- **AND** 它应该将 `decision_path` 设为 "G"
- **AND** 它应该返回最后一个尝试的候选 SQL 和错误信息

### Requirement: 并列最高分按执行时间选最快

任意评分阶段出现并列最高分时，Decision 节点 SHALL 按候选 `execution_time` 升序选择最快的候选，保证决策稳定性。

#### Scenario: 并列选最快
- **GIVEN** 任意评分阶段出现多个并列最高分候选
- **WHEN** 需要从并列组中选 1 个时
- **THEN** 它应该按 `execution_time` 字段升序排列
- **AND** 它应该选择执行时间最短的候选
- **AND** 当多个候选执行时间相同时选择候选列表中位置靠前的（稳定排序）

### Requirement: 评分时失败候选直接剔除

ScoreByData 阶段 SHALL NOT 为执行失败的候选进行评分，以减小 prompt 长度并避免无意义的 LLM 调用。

#### Scenario: 失败候选剔除
- **GIVEN** ScoreByData 阶段
- **WHEN** 过滤候选时
- **THEN** 它应该跳过 `status != SUCCESS` 的候选
- **AND** 它应该不将失败候选送入 LLM 评分
- **AND** 当所有候选都失败时它应该不触发 R1 评分（直接进入全失败分支）

### Requirement: 决策结果包含评分追溯信息

DecisionResult SHALL 扩展字段记录评分过程和决策路径，保持向后兼容的同时提供完整可观测性。

#### Scenario: DecisionResult 字段写入
- **WHEN** Decision 节点完成决策时
- **THEN** 它应该在 `DecisionResult` 中记录 `candidate_scores_r1`（始终有）
- **AND** 它应该在 `DecisionResult` 中记录 `candidate_scores_r2`（R2 触发时有，否则 None）
- **AND** 它应该记录 `selected_candidate_id`（最终选中的候选 ID）
- **AND** 它应该记录 `decision_path`（A/B/C/D/E/F/G/H 之一）
- **AND** 它应该记录 `fix_failed` / `fix_rounds_used` / `last_error`（SmartFix 触发时有）
- **AND** 它应该保留原有的 `selected_sql` / `selected_result` / `execution_time` / `decision_reason` 字段（向后兼容）

### Requirement: 结果可信度验证作为收尾节点

Decision 节点选定最终 SQL 后无论来源（评分直选或 SmartFix）SHALL 进入 result_verifier 验证，保留原"决策 24"的兜底能力。

#### Scenario: 结果验证调用与处理
- **GIVEN** Decision 节点选定最终 SQL 后（无论来自评分直选还是 SmartFix）
- **WHEN** `result_verifier` 存在时
- **THEN** 它应该调用 `result_verifier.verify(user_query, selected_sql, result_sample, mschema)`
- **AND** 它应该将验证结果写入 `decision.voting_summary['verification']`
- **AND** 当验证结果 `should_reject=True` 时它应该在 `DecisionResult.decision_reason` 中标注"结果不可信"
- **AND** 当验证结果 `should_reject=True` 时它应该保留 `selected_sql` / `selected_result` 供 downstream 决策是否使用

## ADDED Requirements

### Requirement: clarification 事件携带结构化 kind 与 options

clarification SSE 事件 SHALL 携带 `kind`（`"confirm" | "choice" | "open"`）字段。`kind` 为 `confirm` 或 `choice` 时，事件 SHALL 携带 `options: {label: string, value: string}[]`（至少 2 项）。`kind` 缺失时前端 MUST 按兼容降级为 `"open"` 处理。`ambiguities` 字段保留以向后兼容，`kind` 存在时前端 MUST 以 `kind`/`options` 为渲染权威来源。

#### Scenario: confirm 事件携带二选一 options
- **WHEN** 后端 emit clarification 事件，`kind="confirm"`
- **THEN** 事件 `options` 含且仅含 2 项（如 `{label:"是，复用",value:"yes"}` 与 `{label:"否，重新生成",value:"no"}`）
- **AND** `ambiguities` 可为空数组（兼容字段，不参与渲染）

#### Scenario: kind 缺失兼容降级
- **WHEN** clarification 事件未携带 `kind` 字段（旧后端）
- **THEN** 前端按 `"open"` 处理，渲染纯输入框（现有行为）
- **AND** 不因缺失 `kind` 报错或中断反问流程

### Requirement: confirm 类型二选一按钮即提交并隐藏输入框

`kind="confirm"` 时，`ClarificationBubble` SHALL 渲染两个主按钮（对应 `options[0]` / `options[1]` 的 `label`），MUST NOT 渲染自由输入框。点击按钮 SHALL 立即以该 option 的 `value` 作为 resume 答案提交，无需二次确认。

#### Scenario: 点击"是"立即提交复用
- **WHEN** 缓存命中反问展示 `confirm` 二按钮，用户点击"是，复用"
- **THEN** 前端以 `value="yes"` 发起 resume 请求
- **AND** 输入框不渲染，用户无法输入 synonym

#### Scenario: 点击"否"立即提交重新生成
- **WHEN** 用户点击"否，重新生成"
- **THEN** 前端以 `value="no"` 发起 resume 请求
- **AND** 后端判定为不复用，走重新生成路径

### Requirement: choice 类型按钮组与可选自定义输入并存

`kind="choice"` 时，`ClarificationBubble` SHALL 将 `options` 渲染为可点击按钮组，点击按钮即以对应 `value` 提交；同时 SHALL 保留自由输入框供用户自定义回答（提交原文）。

#### Scenario: 点击选项按钮提交
- **WHEN** `choice` 反问展示多个选项按钮，用户点击其一
- **THEN** 以该 option 的 `value` 发起 resume
- **AND** 不必使用输入框

#### Scenario: 自定义输入提交原文
- **WHEN** 用户在 `choice` 输入框输入自定义文本并提交
- **THEN** 以输入原文（非任何 option 的 value）发起 resume
- **AND** 后端按字符串兜底匹配处理

### Requirement: open 类型纯输入框

`kind="open"` 时，`ClarificationBubble` SHALL 仅渲染自由输入框（现有行为），MUST NOT 渲染选项按钮。提交内容为用户输入原文。

#### Scenario: 开放反问纯输入
- **WHEN** `kind="open"` 的 clarification 事件到达
- **THEN** 仅渲染输入框 + 提交按钮，无选项按钮
- **AND** 行为与本次改动前一致

### Requirement: cache_confirm 节点下发 confirm 类型

`cache_confirm` 节点的 interrupt payload SHALL 携带 `kind="confirm"` 与 `options=[{label:"是，复用",value:"yes"},{label:"否，重新生成",value:"no"}]`。后端 SHALL 按 `user_choice == "yes"` 判定 `approved`；当 `user_choice` 不在 `{"yes","no"}` 时，SHALL 回退到现有字符串集合匹配 `{"复用","是","yes",...}` 作为兜底（兼容旧前端与测试逃逸）。

#### Scenario: 用户选是判定复用
- **WHEN** resume 携带 `user_choice="yes"`
- **THEN** `cache_confirm_approved=true`，复用缓存 SQL
- **AND** 时间轴 `cache_confirm` 节点摘要"确认复用 ✓"

#### Scenario: 用户选否判定重新生成
- **WHEN** resume 携带 `user_choice="no"`
- **THEN** `cache_confirm_approved=false`，`cache_hit=false`，清空 `cached_sql`
- **AND** 走重新生成路径

#### Scenario: 非标准值回退字符串匹配
- **WHEN** resume 携带 `user_choice="是"`（旧前端或自定义输入，非 `"yes"`/`"no"`）
- **THEN** 后端回退字符串集合匹配，`"是"` 命中集合，`approved=true`
- **AND** 兜底逻辑保证旧客户端仍可复用

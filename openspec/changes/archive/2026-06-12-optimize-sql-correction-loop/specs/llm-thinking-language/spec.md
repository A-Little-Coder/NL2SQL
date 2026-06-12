## ADDED Requirements

### Requirement: LLMClient 自动注入中文思考指令
LLMClient SHALL 在 chat 和 chat_stream 入口统一注入中文思考指令，强制 Qwen3 等支持思考链的模型用中文进行内部推理，提升前端 SSE 展示对中文用户的友好度。

#### Scenario: 自动注入到 system message
- **GIVEN** LLMClient 接收到 chat 或 chat_stream 调用
- **WHEN** `LLM_CHINESE_THINKING` 环境变量为 true（默认值）时
- **THEN** 它应该在发送给 LLM 之前自动注入中文思考指令
- **AND** 当 messages 列表首条为 system role 时它应该将中文思考指令追加到该 system message 内容末尾
- **AND** 当 messages 列表首条不是 system role 时它应该在列表开头插入新的 system message（内容为中文思考指令）
- **AND** 中文思考指令的内容应为："请全程使用中文进行内部思考和推理。"

### Requirement: 通过环境变量关闭中文思考指令
中文思考指令 SHALL 可通过环境变量 `LLM_CHINESE_THINKING=false` 关闭，保留对英文 prompt 调试或 token 优化场景的灵活性。

#### Scenario: 关闭中文思考指令
- **GIVEN** LLMClient 接收到 chat 调用
- **WHEN** `LLM_CHINESE_THINKING` 环境变量为 false 时
- **THEN** 它应该不注入任何中文思考指令
- **AND** messages 列表应保持原样发送

### Requirement: 中文思考指令不修改业务 prompt 内容
中文思考指令 SHALL 在 LLMClient 层统一注入，9 个业务模块的 prompt 文件 MUST NOT 被修改。

#### Scenario: 业务 prompt 不受影响
- **GIVEN** 9 个业务模块的现有 prompt 文件
- **WHEN** 中文思考指令注入逻辑实现后
- **THEN** 它应该不需要修改任何业务 prompt（SQLGenerator / SQLFix / Answerability / ResultVerifier / IR / SS / HistoryCache / MemoryUpdater / SelfConsistency）
- **AND** 业务模块的 system message 内容应保持不变

### Requirement: 中文思考影响 Qwen3 reasoning_content 输出语言
注入中文思考指令后，Qwen3 模型的思考链（reasoning_content）SHALL 主要用中文输出，正文 JSON SHALL 不受影响。

#### Scenario: 思考链输出中文
- **GIVEN** LLMClient 使用 Qwen3 模型且 `enable_thinking=True`
- **WHEN** 调用 chat_stream 且中文思考指令已注入时
- **THEN** Qwen3 返回的 `reasoning_content`（思考链）应主要使用中文
- **AND** SSE 事件 `llm_thinking` 推送给前端的内容应主要为中文
- **AND** 业务正文（`delta.content` 中的 JSON）应保持原 prompt 要求的格式不受影响

### Requirement: 流式与非流式调用共享同一注入逻辑
chat（阻塞）和 chat_stream（流式）入口 SHALL 共享同一辅助方法，保证 CLI 离线脚本与 API 流式服务行为一致。

#### Scenario: 共享注入方法
- **GIVEN** LLMClient 的 chat（阻塞）和 chat_stream（流式）入口
- **WHEN** 任一入口被调用时
- **THEN** 它应该调用同一个 `_inject_chinese_thinking(messages)` 辅助方法
- **AND** CLI 离线脚本（走 chat 阻塞路径）与 API 流式服务（走 chat_stream）应有一致的注入行为

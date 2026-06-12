## MODIFIED Requirements

### Requirement: LLMClient 自动注入中文思考指令

LLMClient SHALL 在 `invoke` / `stream` / `ainvoke` / `astream` 四个公开入口统一注入中文思考指令，强制 Qwen3 等支持思考链的模型用中文进行内部推理；注入逻辑使用 `langchain_core.messages.SystemMessage` 对象操作（不再操作 dict）。

#### Scenario: 自动注入到 SystemMessage（首条已是 SystemMessage）
- **GIVEN** LLMClient 接收到任一入口调用，传入 `messages = [SystemMessage("你是 SQL 专家"), HumanMessage(...)]`
- **WHEN** `LLM_CHINESE_THINKING` 环境变量为 true（默认值）时
- **THEN** 它应该返回新列表 `[SystemMessage("你是 SQL 专家\n\n请全程使用中文进行内部思考和推理。"), HumanMessage(...)]`
- **AND** 原 messages 列表 MUST NOT 被修改

#### Scenario: 自动注入到 SystemMessage（首条不是 SystemMessage）
- **GIVEN** LLMClient 接收到任一入口调用，传入 `messages = [HumanMessage("查询")]`
- **WHEN** `LLM_CHINESE_THINKING` 环境变量为 true 时
- **THEN** 它应该返回新列表 `[SystemMessage("请全程使用中文进行内部思考和推理。"), HumanMessage("查询")]`
- **AND** 中文思考指令的完整文本为："请全程使用中文进行内部思考和推理。"

#### Scenario: 关闭中文思考指令
- **GIVEN** LLMClient 接收到任一入口调用
- **WHEN** `LLM_CHINESE_THINKING` 环境变量为 false 时
- **THEN** 它应该不注入任何中文思考指令
- **AND** messages 列表应保持原样发送

### Requirement: 中文思考指令不修改业务 prompt 内容

中文思考指令 SHALL 在 LLMClient 层统一注入，业务模块的 `ChatPromptTemplate` 定义 MUST NOT 包含中文思考指令文本。

#### Scenario: 业务 prompt 不受影响
- **GIVEN** 业务模块的 `prompts.py` 中的 `ChatPromptTemplate` 定义
- **WHEN** 中文思考指令注入逻辑实现后
- **THEN** 它应该不需要修改任何业务 prompt 模板的 `("system", "...")` 部分
- **AND** 业务 system message 内容应保持原 prompt 设计

### Requirement: 中文思考影响 Qwen3 reasoning_content 输出语言

注入中文思考指令后，Qwen3 模型的思考链 SHALL 主要用中文输出，正文 JSON SHALL 不受影响。

#### Scenario: 思考链输出中文
- **GIVEN** LLMClient 使用 Qwen3 模型且 `LLM_ENABLE_THINKING=true`
- **WHEN** 业务调用 `stream` 且中文思考指令已注入时
- **THEN** Qwen3 返回的 `AIMessageChunk.additional_kwargs["reasoning_content"]` 应主要使用中文
- **AND** 业务侧通过 `stream_with_sse(...)` 推送的 SSE `llm_thinking` 事件内容应主要为中文
- **AND** 业务正文（chunk content）应保持原 prompt 要求的格式不受影响

### Requirement: 流式与非流式调用共享同一注入逻辑

`invoke` / `stream` / `ainvoke` / `astream` 入口 SHALL 共享同一辅助方法 `_inject_chinese_thinking(messages)`，保证 CLI 离线脚本与 API 流式服务行为一致。

#### Scenario: 共享注入方法
- **GIVEN** LLMClient 的四个公开入口方法
- **WHEN** 任一入口被调用时
- **THEN** 它应该调用同一个 `_inject_chinese_thinking(messages: List[BaseMessage]) -> List[BaseMessage]` 辅助方法
- **AND** 同步与异步、阻塞与流式应有一致的注入行为

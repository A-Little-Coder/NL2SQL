# LLM Client Specification

## Purpose

`utils/llm_client.py` 是项目内所有 LLM 调用的统一封装，基于 `langchain_openai.ChatOpenAI`。
对外暴露 LangChain Runnable 风格的接口（`invoke` / `stream` / `ainvoke` / `astream`），
入参用 `BaseMessage`，不接受 dict；流式 yield `(content_chunk, reasoning_chunk)` 二元组，
SSE 推送和文本累积由业务侧通过模块级辅助函数完成。

## Requirements

### Requirement: LLMClient 公开 LangChain Runnable 风格的同步 API

LLMClient SHALL 对外暴露 `invoke(messages, *, as_json=False, temperature=None, max_tokens=None, thinking=None)` 方法，作为同步阻塞调用入口；命名和参数风格 MUST 对齐 LangChain Runnable 协议。

#### Scenario: 阻塞获取纯文本响应
- **WHEN** 业务侧调用 `client.invoke(messages)` 且未传 `as_json`
- **THEN** 它应该返回 `str` 类型的完整 LLM 响应
- **AND** 内部调用 `langchain_openai.ChatOpenAI.invoke(...)` 一次

#### Scenario: 阻塞获取 JSON 响应
- **WHEN** 业务侧调用 `client.invoke(messages, as_json=True)`
- **THEN** 它应该返回 `Dict[str, Any]` 类型的 JSON 解析结果
- **AND** 内部应通过 `bind(response_format={"type": "json_object"})` 强制 JSON 输出模式
- **AND** 解析失败时应回退到正则匹配，最终兜底返回 `{"raw_response": text}`

#### Scenario: 按调用覆盖采样参数
- **WHEN** 业务侧调用 `client.invoke(messages, temperature=0.3, max_tokens=4096)`
- **THEN** 当次调用应使用 0.3 / 4096，不影响其他调用
- **AND** 未传参的字段沿用 `LLMClient.__init__` 中的默认值

#### Scenario: 按调用覆盖 thinking 模式为关闭
- **WHEN** 业务侧调用 `client.invoke(messages, thinking=False)`
- **THEN** 当次调用应通过 `bind(extra_body={"enable_thinking": False})` 关闭思考链
- **AND** 不影响其他未传 `thinking` 的调用仍使用构造时的默认值

#### Scenario: 按调用覆盖 thinking 模式为开启
- **WHEN** 构造 LLMClient 时 `enable_thinking=False`，但业务侧调用 `client.invoke(messages, thinking=True)`
- **THEN** 当次调用应通过 `bind(extra_body={"enable_thinking": True})` 开启思考链
- **AND** 不影响其他未传 `thinking` 的调用仍使用构造时的默认值（False）

#### Scenario: thinking=None 沿用构造时默认值
- **WHEN** 业务侧调用 `client.invoke(messages)` 且未传 `thinking` 参数
- **THEN** 当次调用应使用 `LLMClient.__init__` 中 `enable_thinking` 的值
- **AND** 行为与修改前完全一致（向后兼容）

### Requirement: LLMClient 公开 LangChain Runnable 风格的流式 API

LLMClient SHALL 对外暴露 `stream(messages, *, as_json=False, temperature=None, max_tokens=None, thinking=None)` 方法，作为流式调用入口；每次 yield 一个 `(content_chunk, reasoning_chunk)` 二元组，其中 `content_chunk` 为正文片段、`reasoning_chunk` 为思考链片段，二者均可为 `None`。

#### Scenario: 流式产生正文 + 思考链
- **WHEN** 业务侧迭代 `client.stream(messages)`
- **THEN** 每个 yield 应为 `(Optional[str], Optional[str])` 二元组
- **AND** 当 chunk 含正文时 `content_chunk` 非空
- **AND** 当 chunk 含思考链时 `reasoning_chunk` 非空
- **AND** 业务侧应自行决定是否累积、是否推送 SSE

#### Scenario: 流式调用 thinking=False 时无 reasoning 输出
- **WHEN** 业务侧调用 `client.stream(messages, thinking=False)`
- **THEN** 每个 yield 的 `reasoning_chunk` 应为 `None`
- **AND** `content_chunk` 应正常返回正文片段

#### Scenario: 流式调用不内置 SSE 推送
- **WHEN** 业务侧迭代 `client.stream(messages)`
- **THEN** LLMClient 本身 MUST NOT 调用 `emit_safe(...)` 或写入任何 SSE queue
- **AND** SSE 推送应由业务侧通过辅助函数 `stream_with_sse` 完成

### Requirement: LLMClient 公开异步 API

LLMClient SHALL 同时实现 `ainvoke` 和 `astream` 方法，作为同步 `invoke` / `stream` 的异步对应；签名和返回类型 MUST 与同步版本对齐（异步返回 `Coroutine` / `AsyncIterator`）；`thinking` 参数 MUST 与同步版本行为一致。

#### Scenario: 异步阻塞调用
- **WHEN** 业务侧 `await client.ainvoke(messages, as_json=True)`
- **THEN** 它应该返回 `Dict[str, Any]`
- **AND** 内部调用 `langchain_openai.ChatOpenAI.ainvoke(...)` 一次

#### Scenario: 异步流式调用
- **WHEN** 业务侧 `async for chunk in client.astream(messages):`
- **THEN** 每个 chunk 应为 `(Optional[str], Optional[str])` 二元组
- **AND** 异步与同步流式的 chunk 内容应一致

#### Scenario: 异步调用支持 thinking 覆盖
- **WHEN** 业务侧 `await client.ainvoke(messages, thinking=False)`
- **THEN** 当次调用应关闭思考链，与同步 `invoke(messages, thinking=False)` 行为一致

### Requirement: LLMClient 入参支持 BaseMessage 列表与 PromptValue

LLMClient 的 `invoke` / `stream` / `ainvoke` / `astream` 方法 SHALL 接受 `List[BaseMessage]` 或 `PromptValue` 作为 `messages` 入参；MUST NOT 接受 `List[Dict[str, str]]`。

#### Scenario: BaseMessage 列表入参
- **GIVEN** 业务侧构造 `msgs = [SystemMessage("..."), HumanMessage("...")]`
- **WHEN** 调用 `client.invoke(msgs)`
- **THEN** 它应该正常完成调用并返回结果
- **AND** 内部不进行 dict → BaseMessage 转换（已是目标类型）

#### Scenario: PromptValue 入参
- **GIVEN** 业务侧通过 `ChatPromptTemplate.format_prompt(...)` 得到 `PromptValue`
- **WHEN** 调用 `client.invoke(prompt_value)`
- **THEN** 它应该正常完成调用并返回结果
- **AND** 内部通过 `prompt_value.to_messages()` 转为 `List[BaseMessage]` 后处理

#### Scenario: dict 入参被拒绝
- **GIVEN** 业务侧传入 `[{"role": "user", "content": "x"}]`
- **WHEN** 调用 `client.invoke(dict_msgs)`
- **THEN** 它应该抛出 `TypeError`，明确告知应使用 `BaseMessage`

### Requirement: LLMClient 模块提供文本累积辅助函数

`utils/llm_client.py` 模块 SHALL 提供 `accumulate(stream_iter) -> str` 函数，把 `stream()` 的 yield 元组流累积为完整正文字符串；思考链部分被丢弃。

#### Scenario: 累积正文
- **GIVEN** `stream_iter` yield 顺序为 `[("a", None), (None, "思考"), ("bc", None)]`
- **WHEN** 调用 `accumulate(stream_iter)`
- **THEN** 它应该返回 `"abc"`
- **AND** 思考链片段 `"思考"` 被忽略

### Requirement: LLMClient 模块提供流式 SSE 推送辅助函数

`utils/llm_client.py` 模块 SHALL 提供 `stream_with_sse(stream_iter) -> str` 函数，在累积正文的同时，把思考链片段自动推送为 SSE `llm_thinking` 事件；事件 payload MUST 包含 `node`（来自 `current_node` ContextVar）和 `text`（思考链内容）。

#### Scenario: 思考链自动推 SSE
- **GIVEN** `current_emitter` ContextVar 已设置为有效的 `StreamEmitter`
- **GIVEN** `stream_iter` yield 包含若干思考链 chunk
- **WHEN** 调用 `stream_with_sse(stream_iter)`
- **THEN** 它应该返回累积后的正文字符串
- **AND** 每个非空思考链 chunk 应触发一次 `emit_safe("llm_thinking", {"node": ..., "text": chunk})`

#### Scenario: 无 emitter 时静默累积
- **GIVEN** `current_emitter` ContextVar 为 `None`（如 CLI / 测试场景）
- **WHEN** 调用 `stream_with_sse(stream_iter)`
- **THEN** 它应该正常累积并返回正文
- **AND** 不抛出异常、不发送任何事件

### Requirement: LLMClient 模块提供 JSON 解析辅助函数

`utils/llm_client.py` 模块 SHALL 提供 `parse_json(text) -> Dict[str, Any]` 函数，解析 LLM 输出的 JSON 字符串；解析失败时 SHALL 兜底正则提取首个 `{...}` 块；最终失败 SHALL 返回 `{"raw_response": text}`。

#### Scenario: 正常 JSON 解析
- **WHEN** 调用 `parse_json('{"k": 1}')`
- **THEN** 它应该返回 `{"k": 1}`

#### Scenario: 兜底正则匹配
- **WHEN** 调用 `parse_json('一些前缀文本 {"k": 1} 一些后缀')`
- **THEN** 它应该返回 `{"k": 1}`

#### Scenario: 完全无法解析
- **WHEN** 调用 `parse_json("纯文本无 JSON")`
- **THEN** 它应该返回 `{"raw_response": "纯文本无 JSON"}`
- **AND** 不抛出异常

### Requirement: LLMClient 不暴露底层 ChatOpenAI 实例

LLMClient SHALL 把 `langchain_openai.ChatOpenAI` 实例存于 `self._chat_model`（私有），MUST NOT 提供 `self.client` 或 `self.chat_model` 等公开访问；业务侧 MUST NOT 直接访问底层 LangChain 对象。

#### Scenario: 私有属性命名
- **WHEN** 检查 `LLMClient` 实例的属性
- **THEN** `self._chat_model` 应存在（带下划线前缀）
- **AND** `self.client` 应不存在
- **AND** `self.chat_model` 应不存在

### Requirement: LLMClient 通过 extra_body 启用 Qwen3 思考模式

LLMClient SHALL 在 `__init__` 时将 `enable_thinking` 配置通过 `ChatOpenAI(extra_body={"enable_thinking": True/False})` 顶层参数透传到底层 API；环境变量 `LLM_ENABLE_THINKING=false` 时传 `False`，默认传 `True`。

#### Scenario: 启用思考模式
- **GIVEN** 环境变量 `LLM_ENABLE_THINKING=true`（默认）
- **WHEN** 构造 `LLMClient(...)`
- **THEN** `self._chat_model.extra_body` 应包含 `{"enable_thinking": True}`

#### Scenario: 关闭思考模式
- **GIVEN** 环境变量 `LLM_ENABLE_THINKING=false`
- **WHEN** 构造 `LLMClient(...)`
- **THEN** `self._chat_model.extra_body` 应包含 `{"enable_thinking": False}`

### Requirement: LLMClient 通过 output_version 启用 Qwen reasoning 输出

LLMClient SHALL 在 `__init__` 时把 `ChatOpenAI` 实例配置为 `output_version="responses/v1"`，使 chunk content 输出为 `list[dict]` 结构，从中提取 Qwen3 的 reasoning summary 和正文 text。

#### Scenario: ChatOpenAI 实例配置
- **WHEN** 构造 `LLMClient(...)`
- **THEN** `self._chat_model` 应是 `ChatOpenAI(..., output_version="responses/v1", extra_body=...)`

#### Scenario: 含思考链的 chunk 解析
- **GIVEN** `chunk.content = [{"type": "reasoning", "summary": [{"text": "在思考"}]}]`
- **WHEN** LLMClient 内部解析该 chunk
- **THEN** 它应该把 `"在思考"` 作为 reasoning 片段返回（yield 元组的第二位）

#### Scenario: 含正文的 chunk 解析
- **GIVEN** `chunk.content = [{"type": "text", "text": "abc"}]`
- **WHEN** LLMClient 内部解析该 chunk
- **THEN** 它应该把 `"abc"` 作为正文片段返回（yield 元组的第一位）

#### Scenario: 同一 chunk 含多类型 blocks
- **GIVEN** `chunk.content = [{"type": "reasoning", "summary": [{"text": "思"}]}, {"type": "text", "text": "结果"}]`
- **WHEN** LLMClient 内部解析该 chunk
- **THEN** 它应该 yield `("结果", "思")`（content 和 reasoning 同时返回）

### Requirement: 业务侧 Prompt 全部使用 ChatPromptTemplate

业务侧所有 LLM 调用 SHALL 通过 `langchain_core.prompts.ChatPromptTemplate.from_messages(...)` 构造模板；MUST NOT 使用 f-string 拼接 + dict messages 的旧模式；每个业务模块 SHALL 在自己的 `prompts.py` 中集中定义模板。

#### Scenario: 决策模块的 prompt 集中管理
- **WHEN** 检查 `src/decision/prompts.py`
- **THEN** 它应该存在并定义 `SCORE_BY_DATA_PROMPT` / `SCORE_BY_SQL_PROMPT` 等 `ChatPromptTemplate` 常量
- **AND** `src/decision/self_consistency.py` 应从此处 import 使用

#### Scenario: 业务节点调用模式
- **GIVEN** 节点函数需要调 LLM
- **WHEN** 实现该节点
- **THEN** 它应该 `from src.<module>.prompts import XXX_PROMPT`
- **AND** 通过 `msgs = XXX_PROMPT.format_messages(...)` 得到 `List[BaseMessage]`
- **AND** 通过 `client.invoke(msgs)` 或 `client.stream(msgs)` 调用

### Requirement: 业务节点函数使用 stream + stream_with_sse 组合

在 API SSE 上下文中运行的业务节点函数 SHALL 使用 `client.stream(msgs)` + `stream_with_sse(...)` 组合调用 LLM，保持流式 SSE 推送行为；MUST NOT 在节点函数中直接调用 `client.invoke(...)`（除非该节点专门用于离线场景）。

#### Scenario: 节点函数典型调用模式
- **GIVEN** 一个跑在 API SSE 上下文中的节点函数
- **WHEN** 该节点调用 LLM
- **THEN** 它应该写作 `raw = stream_with_sse(self.llm.stream(msgs, as_json=True))`
- **AND** 后续应调 `result = parse_json(raw)`

### Requirement: 离线脚本使用 invoke 一步到位

离线 / CLI 场景（如 `src/preprocessing/build_schema_graphs.py`）的 LLM 调用 SHALL 使用 `client.invoke(msgs, as_json=True)` 直接获取结果；MUST NOT 在无 SSE 上下文场景使用 `stream` + 累积。

#### Scenario: 离线场景调用模式
- **GIVEN** 一个独立运行的脚本（无 API SSE 上下文）
- **WHEN** 该脚本调用 LLM
- **THEN** 它应该直接 `result = client.invoke(msgs, as_json=True)`
- **AND** MUST NOT 引入 `stream_with_sse` 等流式辅助函数

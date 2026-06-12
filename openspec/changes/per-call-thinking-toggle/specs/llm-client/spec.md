## MODIFIED Requirements

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

## REMOVED Requirements

### Requirement: LLM 最终决策方法
**Reason**: 决策 51 两段式评分（R1 数据评分 + R2 SQL 评分 + SmartFix）已完全取代 `llm_final_decision`。新决策子图 `decision_graph.py` 不走此路径。
**Migration**: 使用 `SelfConsistencyDecision.build_graph()` 入口，内部通过 R1/R2 评分 + SmartFix 完成决策。

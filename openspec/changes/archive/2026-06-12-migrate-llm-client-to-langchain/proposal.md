## Why

当前 `utils/llm_client.py` 用裸 `openai.OpenAI` SDK 直连 Qwen API。这条路径无法享受 LangChain 1.x 生态能力（统一 Runnable 协议、`ChatPromptTemplate` 模板、`LangSmith` 自动 trace、`bind_tools` / `with_structured_output` 等）。同时项目内已有 `langgraph 1.2.2` 和 `langchain-core 1.4.0`（langgraph 间接拉入），LLMClient 是唯一脱离这套生态的模块，造成"半套 LangChain"的尴尬。本期把 LLMClient 内部统一迁移到 `langchain_openai.ChatOpenAI`，并把接口与入参类型彻底对齐 LangChain 标准，为后续接入 LangSmith 监控、Prompt 模板化、工具调用扫清道路。

## What Changes

- **BREAKING**：`LLMClient` 公开 API 改名为 LangChain Runnable 风格：`invoke` / `stream` / `ainvoke` / `astream`，删除旧 `chat` / `chat_json` / `chat_stream`
- **BREAKING**：`LLMClient` 入参类型从 `List[Dict[str, str]]` 改为 `List[BaseMessage] | PromptValue`，业务侧统一用 `SystemMessage` / `HumanMessage` 构造
- **BREAKING**：删除 `self.client` 属性（不再暴露裸 OpenAI 客户端）
- 用 `as_json=True` 关键字参数控制 JSON 模式（替代旧 `response_format` 透传）
- `stream()` yield `(content_chunk, reasoning_chunk)` 二元组，业务侧自行累积、自行推 SSE
- 新增模块级工具函数：`accumulate(stream_iter)` / `stream_with_sse(stream_iter)` / `parse_json(text)`，集中放在 `utils/llm_client.py`
- 中文思考指令注入仍在 `LLMClient` 入口统一处理（改用 `SystemMessage` 对象操作）
- 所有业务 prompt 改用 `langchain_core.prompts.ChatPromptTemplate.from_messages`，按模块拆分到各自 `prompts.py`（决策 / SQL 生成 / 检索 / Schema 选择 / 验证 / 执行 / 记忆 共 7 个）
- 业务调用一次性全改（13+ 处），节点函数（API 上下文）用 `stream`，离线脚本用 `invoke`
- `requirements.txt` 新增 `langchain-openai >= 1.0.0, < 2.0.0`，把 `langchain-core` 从间接依赖转显式锁定

## Capabilities

### New Capabilities

无（本期不引入新的对外能力，纯重构）

### Modified Capabilities

- `llm-client`：LLMClient 对外接口契约全量修改 —— 方法名、入参类型、JSON 模式控制方式、辅助函数、Prompt 模板组织方式全部变更
- `llm-thinking-language`：中文思考指令注入实现从 `dict` 操作改为 `BaseMessage` 对象操作，对外行为不变

## Impact

**代码改动**：
- `utils/llm_client.py`：完全重写（约 300 行）
- 7 个新增 `prompts.py` 文件（`src/decision/prompts.py` 等）
- 13 个业务模块的 LLM 调用点全量改写
- `tests/api/test_streaming.py` 重写 mock（`AIMessageChunk` 替代裸 chunk Mock）
- 业务测试约 10 个文件批量替换 mock 方法名

**依赖改动**：
- `requirements.txt` 新增 `langchain-openai >= 1.0.0, < 2.0.0`
- `requirements.txt` 显式锁定 `langchain-core >= 1.0.0, < 2.0.0`（之前是 langgraph 间接拉入）
- 不动 `openai` / `pydantic` / `langgraph` 版本

**对外行为**：
- SSE 事件流（`llm_thinking` 等）语义完全不变
- 决策 50 / 51 的"中文流式思考链"成果完全保留
- API HTTP 接口零变化
- 测试覆盖范围零下降（替换 mock 方式但用例数不减）

**风险**：
- `langchain-openai 1.x` 较新，Qwen3 `reasoning_content` 字段在 `AIMessageChunk` 内的具体位置需要探测脚本一次性验证；探测通过后字段位置写死
- 业务侧迁移引入"手动累积 + 推 SSE"的模板代码，需通过 `stream_with_sse` 辅助函数收敛

## Context

**当前状态**：
- `utils/llm_client.py` 直接用 `openai.OpenAI` SDK 连接 Qwen API（DashScope 兼容接口）
- 项目编排层用 `langgraph 1.2.2`，它强依赖 `langchain-core 1.4.0`（已被间接拉入安装）
- LLMClient 是项目内**唯一**脱离 LangChain 生态的模块
- 已有 3 个决策（49 流式 SSE / 50 思考链推送 / 51 中文思考指令）紧紧绑定在裸 SDK 路径上
- 业务侧 13 处 `chat_json(...)` 调用 + 2 处 `chat(..., response_format=...)` 调用
- 所有 prompt 都是顶层常量 + f-string + 手工 dict messages 拼装

**已与用户对齐的迁移基调**：
- 一次性彻底迁移，不保留旧接口兼容期
- 业务调用全量改写（13+ 处）
- 入参类型从 `dict` 升级到 `BaseMessage`，"对齐 LangChain 就对齐到底"
- 锁版本即视为字段位置确定，不做防御性多层兼容

## Goals / Non-Goals

**Goals**：
- LLMClient 内部完全基于 `langchain_openai.ChatOpenAI`，对齐 LangChain Runnable 协议
- 公开接口语义清晰、命名规范、与 LangChain 一致（`invoke` / `stream` / `ainvoke` / `astream`）
- 业务侧 prompt 全部模板化（`ChatPromptTemplate.from_messages`）
- SSE 推送、文本累积、JSON 解析的职责清晰分层（LLM 服务只做 LLM 调用）
- 决策 49/50/51 的对外行为零变化（用户体验不感知）
- 版本依赖不破坏既有 `langgraph` / `pydantic` / `openai` 的安装版本

**Non-Goals**：
- 不本期接入 LangSmith 监控（独立后续 change）
- 不引入 LangChain 工具调用（`bind_tools`） / 结构化输出（`with_structured_output`）等高级能力（后续按需）
- 不重构主图节点拓扑（`StateGraph` 不动）
- 不引入 `Chain` / `Pipeline` 等 LCEL 流水线语法（业务调用仍是命令式）
- 不替换业务的中文 prompt 文案（决策 51 已对齐，文案保持不变）

## Decisions

### 决策 1：公开 API 命名采用 LangChain Runnable 风格

**决策**：
```
invoke(messages, *, as_json=False, temperature=None, max_tokens=None) -> str | Dict
stream(messages, *, as_json=False, temperature=None, max_tokens=None) -> Iterator[Tuple[Optional[str], Optional[str]]]
ainvoke(...)   -> async 版 invoke
astream(...)   -> async 版 stream
```

**理由**：
- 内部就是 `ChatOpenAI`，命名对齐 Runnable 协议有自解释性
- 未来若引入 `Runnable | parser` 流水线，业务零迁移
- `chat_json` 这种"返回类型混进方法名"的写法不规范

**备选**：
- `complete_text` / `complete_json`（按返回类型）：被否，理由是耦合返回类型
- 保留 `chat_xxx`：被否，理由是命名不对齐 LangChain

### 决策 2：入参类型用 `List[BaseMessage] | PromptValue`

**决策**：
- 业务侧 `from langchain_core.messages import SystemMessage, HumanMessage` 构造消息
- LLMClient 同时接受 `ChatPromptTemplate.format_prompt()` 返回的 `PromptValue`
- 不再接受 `List[Dict[str, str]]`

**理由**：
- 类型安全、IDE 自动补全友好
- `ChatPromptTemplate.format_messages()` 直接产出 `List[BaseMessage]`，零转换
- 中文注入逻辑用 `isinstance(msg, SystemMessage)` 比 `msg["role"] == "system"` 优雅

**备选**：
- 继续接 `dict`：被否，多一层无意义转换，丢失类型信息

### 决策 3：JSON 模式用 `as_json=True` keyword 参数

**决策**：
- `invoke(msgs, as_json=True)` 内部调 `bind(response_format={"type": "json_object"})` + `parse_json()`
- `invoke(msgs)` 默认返回纯字符串
- `stream(msgs, as_json=True)`：底层仍流式，最终业务侧 `accumulate` 后再 `parse_json`

**理由**：
- 业务关心的就是"要不要 JSON 输出"，用一个布尔开关最直接
- 不暴露 LangChain 的 `with_structured_output(Pydantic)` 高级用法（本期 YAGNI）
- 与现有调用习惯（旧 `chat_json`）兼容性最好

**备选**：
- 用 `with_structured_output`：被否，需要业务定义 Pydantic schema，迁移成本高
- 通过 `bind()` 让业务自行控制：被否，每个调用点都要重复 boilerplate

### 决策 4：`stream()` yield `(content_chunk, reasoning_chunk)` 元组

**决策**：
```python
def stream(...) -> Iterator[Tuple[Optional[str], Optional[str]]]:
    for chunk in self._chat_model.bind(**kw).stream(messages):
        content = chunk.content or None
        reasoning = self._extract_reasoning(chunk)
        yield (content, reasoning)
```

**理由**：
- 业务关心两件事：正文 token、思考链 token，二元组直接对应
- 业务侧可自由选择处理方式：`accumulate` / `stream_with_sse` / 自定义
- 比 yield 原始 `AIMessageChunk` 更易测试（不耦合 LC 内部类型变化）

**备选**：
- 只 yield 正文，reasoning 另开接口：被否，二者来源同一 chunk，分开拿浪费
- yield 原始 `AIMessageChunk`：被否，泄露 LangChain 类型，业务侧要 `import` 多余的东西

### 决策 5：辅助函数 `accumulate` / `stream_with_sse` / `parse_json` 模块级 + 同文件

**决策**：
```python
# utils/llm_client.py 同文件，模块级函数
def accumulate(stream_iter) -> str
def stream_with_sse(stream_iter) -> str  # 累积 + 推 llm_thinking
def parse_json(text) -> Dict[str, Any]
```

**理由**：
- 业务侧不必"知道 LLMClient 类",直接 `from utils.llm_client import stream_with_sse`
- 三个函数与 LLM 客户端语义紧密，放同文件清晰
- 业务侧写法：
  ```python
  raw_text = stream_with_sse(self.llm.stream(msgs, as_json=True))
  result = parse_json(raw_text)
  ```

**备选**：
- 全部做成 `LLMClient` 方法：被否，业务侧操作流式结果时不该绕回客户端实例
- 放独立 `utils/llm_helpers.py`：被否，过度拆分

### 决策 6：Prompt 全部模板化（`ChatPromptTemplate.from_messages`）

**决策**：
- 每个业务模块新增一个 `prompts.py`，集中放该模块所有 `ChatPromptTemplate`
- 业务节点函数：
  ```python
  from src.decision.prompts import SCORE_BY_DATA_PROMPT
  msgs = SCORE_BY_DATA_PROMPT.format_messages(user_query=..., candidates_text=...)
  raw = stream_with_sse(self.llm.stream(msgs, as_json=True))
  ```

**新增文件清单**：
- `src/decision/prompts.py`（5 个模板：`SCORE_BY_DATA` / `SCORE_BY_SQL` / `LLM_FINAL_DECISION` / `RESULT_VERIFY` 等）
- `src/sql_generation/prompts.py`（2 个）
- `src/retrieval/prompts.py`（2 个）
- `src/schema_selection/prompts.py`（2 个）
- `src/verification/prompts.py`（2 个）
- `src/execution/prompts.py`（1 个：`SQL_FIX`）
- `src/memory/prompts.py`（2 个）

**理由**：
- f-string 漏变量不报错，模板缺变量立即 `KeyError`
- 模板和业务逻辑分离，未来 prompt 调优只改 `prompts.py`
- 为后续 `LCEL` 流水线、`partial` 提前 bind 留口子

**备选**：
- 顶层 `src/prompts/` 集中：被否，跨模块依赖
- 保持现状 f-string：被否，不对齐 LangChain

### 决策 7：中文思考指令注入仍在 `LLMClient` 入口集中处理

**决策**：
```python
# utils/llm_client.py
def _inject_chinese_thinking(self, messages: List[BaseMessage]) -> List[BaseMessage]:
    if not _CHINESE_THINKING_DEFAULT:
        return messages
    if not messages:
        return [SystemMessage(_CHINESE_INSTRUCTION)]
    first = messages[0]
    if isinstance(first, SystemMessage):
        merged = first.content.rstrip() + "\n\n" + _CHINESE_INSTRUCTION
        return [SystemMessage(merged)] + list(messages[1:])
    return [SystemMessage(_CHINESE_INSTRUCTION)] + list(messages)
```

**理由**：
- 业务侧 16 个 prompt 模板都加一遍 system 段过于啰嗦
- 集中注入便于 `LLM_CHINESE_THINKING=false` 环境变量一处关闭
- 决策 51 的对外行为完全保留

**备选**：
- 每个 `ChatPromptTemplate` 自带中文 system 段：被否，重复
- 在 `ChatOpenAI` 实例化时 bind：被否，bind 不能动态合并已有 system

### 决策 8：使用 `output_version="responses/v1"` + 解析 chunk.content 的 block 结构

**决策**：
- `ChatOpenAI.__init__` 时显式传 `output_version="responses/v1"`（langchain-openai 0.3.26+ 引入的新格式）
- 配合 `extra_body={"enable_thinking": True}` 透传给 Qwen
- 此时 `chunk.content` 不再是字符串，而是 `list[dict]`，每个 dict 有 `type` 字段（`"reasoning"` 或 `"text"`）
- `_extract_reasoning` / 内容累积都从 `chunk.content` 这个 list 里按 type 分别处理

```python
# 流式解析示例
for chunk in self._chat_model.stream(messages):
    for block in (chunk.content or []):
        if not isinstance(block, dict):
            continue
        if block.get("type") == "reasoning":
            for s in block.get("summary", []):
                if isinstance(s, dict) and s.get("text"):
                    reasoning_text += s["text"]
        elif block.get("type") == "text":
            content_text += block.get("text", "")
```

**理由**：
- 探测脚本验证：`langchain-openai 1.3.0` 的 `ChatOpenAI` 官方明确**不解析** `additional_kwargs["reasoning_content"]`（源码注释：第三方扩展字段不保留）
- 但 `output_version="responses/v1"` 模式下，LangChain 把 reasoning 和正文 text 都作为 content blocks 输出
- 实测 149 个 chunks 完整拿到 reasoning + content 双流，与裸 OpenAI SDK 等价
- 这是 LangChain 官方推荐的"新格式"（langchain-openai 0.3.26+ 起新增），向后行为稳定

**备选**：
- ~~从 `additional_kwargs.reasoning_content` 取~~：被否，1.x 已不保留该字段
- ~~从 `response_metadata` 取原始 raw body~~：被否，response_metadata 只存 token usage / headers，不存 raw body
- 用 `langchain-qwq` 第三方包：被否，要求 Python 3.11+（当前项目是 3.10），且引入新厂商依赖维护成本
- ~~降级到 langchain-openai 0.x~~：被否，会强制降级 langchain-core / langgraph

**字段差异对照**：

| 维度 | 裸 OpenAI SDK | ChatOpenAI 1.x + output_version=responses/v1 |
|------|--------------|---------------------------------------------|
| chunk 类型 | `ChatCompletionChunk` | `AIMessageChunk` |
| content 类型 | `str` | `list[dict]` |
| reasoning 位置 | `delta.reasoning_content` | `content` 里 `type="reasoning"` 的 block 的 `summary[i].text` |
| 正文位置 | `delta.content` | `content` 里 `type="text"` 的 block 的 `text` |

### 决策 9：业务侧场景分流（节点用 stream，离线用 invoke）

**决策**：
- 业务节点（在 API SSE 上下文中跑）：用 `stream` + `stream_with_sse`，让用户看到流式思考
- 离线脚本（`src/preprocessing/build_schema_graphs.py` 等）：用 `invoke`，一步到位
- 业务代码硬编码方式选择，不做"自适应"

**理由**：
- 显式调用 `stream` / `invoke` 比"emitter 隐式切换"更可读
- 测试 mock 不会因为 emitter 状态变化而失败
- 离线场景无人看 SSE，省一次流式开销

**备选**：
- 加 `LLMClient.auto()` 自适应方法：被否，引入隐式行为
- 全用 `stream`（离线场景也累积返回）：被否，离线场景无意义开销

### 决策 10：版本锁定 `langchain-openai >= 1.0.0, < 2.0.0`

**决策**：
- `requirements.txt` 新增：
  - `langchain-openai>=1.0.0,<2.0.0`
  - `langchain-core>=1.0.0,<2.0.0`（从间接依赖转显式）
- `openai>=2.0.0,<3.0.0`（已装 2.38.0，由 langchain-openai 依赖，显式锁主版本）
- 具体 1.x.y 版本号在 Step 1 探测时确定（pip 解出来的最新即锁定）

**理由**：
- 已装 `langchain-core 1.4.0` + `langgraph 1.2.2`，必须配 `langchain-openai 1.x` 否则会强制降级
- 主版本号锁定（`<2.0.0`）防止未来 LangChain 大版本升级误伤
- `>=X.0.0` 而不是 `==X.Y.Z`：允许补丁更新，但出问题时锁死具体版本

**备选**：
- 锁死 `==1.0.0`：被否，过严
- 不锁版本（`>=`）：被否，未来升级破坏行为

### 决策 11：测试 mock 改造采用策略 B

**决策**：
- 直接 mock `client._chat_model.invoke` / `client._chat_model.stream`
- `tests/api/test_streaming.py` 的 `fake_stream` 用 `AIMessageChunk` 构造
- 业务测试批量替换 `chat_json` mock 为 `invoke` mock

**理由**：
- 业务测试本来就是 mock `LLMClient.chat_json.return_value`，把方法名换掉即可
- 流式测试需要构造真实的 `AIMessageChunk`，但只此一个文件

**备选**：
- 抽 `_raw_invoke / _raw_stream` 内部方法做 mock 锚点：被否，引入无意义抽象层

### 决策 12：异步 API 本期真实现

**决策**：
- `ainvoke` / `astream` 不是骨架占位，而是真实调 `self._chat_model.ainvoke / astream` 的薄壳
- 同步版本所有逻辑都对应有异步版本（_inject_chinese_thinking、_extract_reasoning 复用）

**理由**：
- LangChain 已经免费提供异步实现，几行代码包一层即可
- 本期不实现，未来要用 API 异步路径时会卡住

## Risks / Trade-offs

| 风险 | 影响 | 缓解 |
|------|------|------|
| `langchain-openai 1.x` 较新，Qwen3 `reasoning_content` 字段位置未经官方确认 | 思考链推送可能失效 | Step 1 探测脚本强制验证，通过才进入 Step 2；探测失败回退到字段名探查 |
| 业务侧 13+ 处调用一次性全改，无灰度期 | 出 bug 时 blast radius 大 | 严格走 Step 3-4-5 流程：先 prompt 模板化、再调用迁移、最后真实回归；每个 commit 都跑全量 pytest |
| `ChatOpenAI` 对 Qwen `extra_body.enable_thinking` 的支持依赖 `model_kwargs` 透传 | 思考模式关闭 | Step 1 探测同时验证 enable_thinking 是否生效 |
| `BaseMessage` 入参后业务侧需 import 新类，迁移略繁琐 | 一次性人力成本 | 用 sed 批量 + 手工 review；每个文件改完单独跑测试 |
| `requirements.txt` 引入 `langchain-openai` 后 `pip install` 可能拉额外间接依赖 | 部署包体积上升 | Step 1 在干净 venv 装一次记录新增包；预估 ~50 MB |
| 异步 `ainvoke` / `astream` 本期实现但无测试覆盖 | 后续才发现 bug | tasks.md 中明确：异步 API 加 smoke test（mock 路径走通即可） |
| `stream_with_sse` 辅助函数和业务代码的耦合（依赖 `current_emitter` ContextVar） | 测试时易漏设 emitter | 测试用 `stream_with_sse` 时显式 set emitter；文档明确说明 |
| 决策 50/51 的 SSE 推送从 `LLMClient` 内部移到业务侧 `stream_with_sse` | 节点函数变啰嗦 | 包装成单行调用 `text = stream_with_sse(llm.stream(msgs))`；提供示例文档 |

## Migration Plan

**Step 1：环境探测 + 版本锁定**（半天）
1. 在干净 venv 装 `langchain-openai`，记录 pip 解出的最新 1.x 版本
2. 写 `scripts/probe_chatopenai_reasoning.py`，真实调 Qwen3 验证：
   - `reasoning_content` 在 `AIMessageChunk.additional_kwargs` 内的存在性
   - `model_kwargs={"extra_body": {"enable_thinking": True}}` 是否生效
   - `bind(response_format={"type": "json_object"})` 是否生效
   - `ainvoke` / `astream` 是否可用
3. 探测通过 → 锁定 `requirements.txt`；探测失败 → 调研 Qwen3 在 LC 1.x 下的字段位置，更新决策 8

**Step 2：重写 `utils/llm_client.py`**（半天）
1. 引入 `ChatOpenAI` + `BaseMessage` 等
2. 实现 `invoke` / `stream` / `ainvoke` / `astream` 四个公开方法
3. 实现 `_inject_chinese_thinking` / `_extract_reasoning` / `_build_runtime_kwargs` 私有方法
4. 实现 `accumulate` / `stream_with_sse` / `parse_json` 模块级工具函数
5. 删除 `self.client` 属性、删除 `chat` / `chat_json` / `chat_stream` 旧接口
6. 添加新版 `LLMClient` 的单元测试

**Step 3：Prompt 模板化**（半天）
1. 按模块创建 7 个 `prompts.py`
2. 抽取约 14-16 个 `ChatPromptTemplate.from_messages` 模板
3. 验证模板的 `format_messages()` 输出符合预期

**Step 4：业务调用全量迁移**（1 天）
1. 按文件迁移：每个文件改完单独跑相关测试
2. 节点函数：`raw = stream_with_sse(self.llm.stream(msgs, as_json=True))` + `parse_json(raw)`
3. 离线脚本：`result = self.llm.invoke(msgs, as_json=True)`
4. 删除所有 `chat_json(...)` / `chat(...)` 调用

**Step 5：测试改造 + 真实回归**（半天）
1. 重写 `tests/api/test_streaming.py` 的 `fake_stream`（用 `AIMessageChunk`）
2. 业务测试批量 `sed` 替换 mock 方法名
3. `pytest` 全量通过
4. 启动 API 发真实 query，确认 SSE `llm_thinking` 事件中文输出正常
5. 离线脚本（如 `build_schema_graphs`）跑一次，确认 `invoke` 正常
6. `git rm scripts/probe_chatopenai_reasoning.py`

**回滚策略**：
- 每个 Step 完成后单独 commit，便于按 step 回滚
- 如发现 `langchain-openai` 严重 bug，最坏情况 `git revert` 整个 change，恢复裸 OpenAI SDK 路径
- `requirements.txt` 改动可独立回滚（pip 重装即可）

## Open Questions

- Step 1 探测后才确定的：`langchain-openai` 的精确版本号（如 `1.0.3`）—— 由探测结果填入 tasks.md 和 requirements.txt
- 异步 `ainvoke` / `astream` 是否需要在本期接入到任何业务调用点？（倾向"否"，纯接口预留；如有业务需求请告知）
- `stream_with_sse` 的事件类型固定为 `llm_thinking`，与当前 SSE 协议一致；是否未来需要支持业务自定义事件类型？（暂不考虑）

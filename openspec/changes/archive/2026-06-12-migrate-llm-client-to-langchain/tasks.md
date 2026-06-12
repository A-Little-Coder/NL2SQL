# Implementation Tasks

## 1. Step 1 — 环境探测 + 版本锁定

- [x] 1.1 在干净 venv（或当前 venv）执行 `pip install "langchain-openai>=1.0.0,<2.0.0" -i https://pypi.tuna.tsinghua.edu.cn/simple`
- [x] 1.2 记录 pip 解出的实际 `langchain-openai` 精确版本（如 `1.0.3`），并确认 `langchain-core` 未被降级（应仍为 `1.4.0`）—— 实际：`langchain-openai 1.3.0` + `langchain-core 1.4.6`（小补丁升级，向后兼容）
- [x] 1.3 创建 `scripts/probe_chatopenai_reasoning.py` 探测脚本（用完即删，不入库长期维护）
- [x] 1.4 探测脚本验证项 1：流式 `model.stream([HumanMessage("北京今天天气")])` 中 `chunk.additional_kwargs.get("reasoning_content")` 存在且非空 —— **方案调整**：1.x 不再保留 reasoning_content 字段，改用 `output_version="responses/v1"` + 解析 `chunk.content` 的 `list[dict]`（详见 design.md 决策 8）
- [x] 1.5 探测脚本验证项 2：`ChatOpenAI(model_kwargs={"extra_body": {"enable_thinking": True}})` 真实启用思考模式（关闭时 reasoning_content 为空）—— 实测 enable_thinking=True 时 64 reasoning blocks，False 时 0 个
- [x] 1.6 探测脚本验证项 3：`model.bind(response_format={"type": "json_object"}).invoke([...])` 强制 JSON 输出生效
- [x] 1.7 探测脚本验证项 4：`await model.ainvoke([...])` 和 `async for chunk in model.astream([...]):` 异步路径可用
- [x] 1.8 更新 `requirements.txt`：新增 `langchain-openai>=1.0.0,<2.0.0` 和 `langchain-core>=1.0.0,<2.0.0` 显式锁定；锁定 `openai>=2.0.0,<3.0.0`
- [x] 1.9 探测全部通过 → commit 1：`feat(deps): 锁定 langchain-openai 1.x，环境探测脚本通过`
- [x] 1.10 若探测失败（如 reasoning_content 不在 additional_kwargs）→ 暂停后续步骤，更新 `design.md` 决策 8 —— **已触发**：1.x 不再保留 reasoning_content，已更新 design.md 决策 8 和 specs/llm-client/spec.md 对应 Requirement

## 2. Step 2 — 重写 LLMClient

- [x] 2.1 备份当前 `utils/llm_client.py`（git 已是版本控制，此为心理保险）
- [x] 2.2 重写 `utils/llm_client.py`：导入 `from langchain_openai import ChatOpenAI` / `from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage` / `from langchain_core.prompt_values import PromptValue`
- [x] 2.3 重写 `LLMClient.__init__`：构造 `self._chat_model = ChatOpenAI(...)`，把 `enable_thinking` 通过 `extra_body` 顶层参数透传（决策 8 修正：不是 model_kwargs）；删除 `self.client` 别名；新增 `output_version="responses/v1"`
- [x] 2.4 实现私有方法 `_inject_chinese_thinking(messages: List[BaseMessage]) -> List[BaseMessage]`（用 `SystemMessage` 对象操作）
- [x] 2.5 实现私有方法 `_normalize_messages(messages_or_prompt_value) -> List[BaseMessage]`：接受 `List[BaseMessage]` 或 `PromptValue`，dict 入参抛 `TypeError`
- [x] 2.6 实现私有方法 `_bind_runtime(temperature, max_tokens, as_json) -> Runnable`：组装 `bind()` 参数并返回绑定后的 Runnable（也是测试 mock 锚点）
- [x] 2.7 实现私有方法 `_extract_chunk_blocks(chunk) -> Tuple[Optional[str], Optional[str]]`：解析 `chunk.content` 的 `list[dict]`，按 type 分别拿 text / reasoning（决策 8 修正：不是 `additional_kwargs.reasoning_content`）；另有 `_extract_text_from_message(AIMessage)` 处理阻塞 invoke 返回
- [x] 2.8 实现公开方法 `invoke(messages, *, as_json=False, temperature=None, max_tokens=None) -> Union[str, Dict]`
- [x] 2.9 实现公开方法 `stream(messages, *, as_json=False, temperature=None, max_tokens=None) -> Iterator[Tuple[Optional[str], Optional[str]]]`
- [x] 2.10 实现公开方法 `async def ainvoke(...)` —— 调底层 `self._chat_model.ainvoke(...)`
- [x] 2.11 实现公开方法 `async def astream(...)` —— 调底层 `self._chat_model.astream(...)`
- [x] 2.12 在同文件实现模块级函数 `accumulate(stream_iter) -> str`
- [x] 2.13 在同文件实现模块级函数 `stream_with_sse(stream_iter) -> str`（依赖 `src.api.streaming.current_emitter` ContextVar）
- [x] 2.14 在同文件实现模块级函数 `parse_json(text) -> Dict[str, Any]`
- [x] 2.15 删除旧公开 API：`chat` / `chat_json` / `chat_stream` / 私有 `_chat_blocking` / `_parse_json` / `_has_emitter` / `_inject_chinese_thinking`（dict 版）—— 旧测试 `tests/utils/test_llm_client_chinese_thinking.py` 也删除
- [x] 2.16 编写新 LLMClient 单元测试：`tests/utils/test_llm_client_new.py` 覆盖 invoke / stream / ainvoke / astream / 三个辅助函数 / 中文注入 / Pydantic ChatOpenAI mock 通过 `patch.object(client, "_bind_runtime")` 锚点 —— **35/35 通过**
- [x] 2.17 单元测试全部通过 → commit 2：`refactor(llm_client): 内部迁移到 ChatOpenAI，对外暴露 invoke/stream/ainvoke/astream`

## 3. Step 3 — Prompt 模板化

- [x] 3.1 创建 `src/decision/prompts.py`：抽取 `self_consistency.py` 中所有 LLM prompt（约 4-5 个）为 `ChatPromptTemplate.from_messages` 模板
- [x] 3.2 创建 `src/sql_generation/prompts.py`：抽取 `sql_generator.py` / `cg_graph.py` 中的 prompt（约 2 个）
- [x] 3.3 创建 `src/retrieval/prompts.py`：抽取 `information_retrieval.py` 中的 prompt（约 1-2 个）
- [x] 3.4 创建 `src/schema_selection/prompts.py`：抽取 `schema_selector.py` 中的 prompt（约 1-2 个）
- [x] 3.5 创建 `src/verification/prompts.py`：抽取 `answerability.py` / `result_verifier.py` 中的 prompt（约 2 个）
- [x] 3.6 创建 `src/execution/prompts.py`：抽取 `executor.py` 的 `SQL_FIX_PROMPT`（1 个）
- [x] 3.7 创建 `src/memory/prompts.py`：抽取 `memory_updater.py` / `history_cache.py` 的 prompt（约 2 个）
- [x] 3.8 每个 `prompts.py` 中的模板用 `("system", "...")` / `("user", "...")` 拆分（system 段不再含 JSON 输出指令时移至 user 段尾）
- [x] 3.9 每个 prompts.py 写一个 smoke test：`tests/<module>/test_prompts.py`，断言 `TEMPLATE.format_messages(...)` 输出含期望字段
- [x] 3.10 所有 prompts.py 创建完成 → commit 3：`feat(prompts): 各业务模块新增 prompts.py，集中管理 ChatPromptTemplate`

## 4. Step 4 — 业务调用全量迁移

### 4.1 决策模块（self_consistency.py）

- [x] 4.1.1 `src/decision/self_consistency.py`：替换 `llm_final_decision()` 中的 LLM 调用 → 用 `LLM_FINAL_DECISION_PROMPT.format_messages(...)` + `stream_with_sse(self.llm_client.stream(msgs, as_json=True))` + `parse_json(raw)`
- [x] 4.1.2 `score_by_data()` 同样迁移：用 `SCORE_BY_DATA_PROMPT`
- [x] 4.1.3 `score_by_sql()` 同样迁移：用 `SCORE_BY_SQL_PROMPT`
- [x] 4.1.4 删除所有 `chat_json(...)` 调用残留
- [x] 4.1.5 跑相关单元测试 `pytest tests/decision -x` 通过

### 4.2 执行模块（executor.py）

- [x] 4.2.1 `src/execution/executor.py`：`_try_fix()` 中的 LLM 调用 → 用 `SQL_FIX_PROMPT` + `stream_with_sse(...)` + `parse_json(...)`
- [x] 4.2.2 跑 `pytest tests/execution -x` 通过

### 4.3 验证模块（verification/）

- [x] 4.3.1 `src/verification/answerability.py`：替换 `chat_json` 调用 → 用 `ANSWERABILITY_PROMPT` + stream 组合
- [x] 4.3.2 `src/verification/result_verifier.py`：同上 → 用 `RESULT_VERIFY_PROMPT`
- [x] 4.3.3 跑 `pytest tests/verification -x` 通过

### 4.4 检索 + Schema 选择（retrieval / schema_selection）

- [x] 4.4.1 `src/retrieval/information_retrieval.py`：替换 `chat_json` → 用 `KEYWORD_EXTRACT_PROMPT`（或同名）+ stream 组合
- [x] 4.4.2 `src/schema_selection/schema_selector.py`：替换 `chat_json` → 用 `SCHEMA_SELECT_PROMPT` + stream 组合
- [x] 4.4.3 跑 `pytest tests/retrieval tests/schema_selection -x` 通过

### 4.5 SQL 生成（sql_generation/）

- [x] 4.5.1 `src/sql_generation/sql_generator.py`：替换 `chat_json` → 用 `SQL_GENERATE_PROMPT` + stream 组合
- [x] 4.5.2 `src/sql_generation/cg_graph.py`：替换 `chat_json` → 用 `CG_PROMPT` + stream 组合
- [x] 4.5.3 跑 `pytest tests/sql_generation -x` 通过

### 4.6 记忆模块（memory/）

- [x] 4.6.1 `src/memory/memory_updater.py`：替换 `chat(messages, response_format={"type":"json_object"})` → 用 `MEMORY_UPDATE_PROMPT` + invoke as_json=True（记忆更新是离线/异步场景，无需 SSE）
- [x] 4.6.2 `src/memory/history_cache.py`：同上 → 用 `HISTORY_CACHE_PROMPT`
- [x] 4.6.3 跑 `pytest tests/memory -x` 通过

### 4.7 离线预处理（preprocessing/）

- [x] 4.7.1 `src/preprocessing/build_schema_graphs.py`：保持 `LLMClient` 初始化方式但改用 `invoke(...)` 直接获取结果（无 SSE 场景）
- [x] 4.7.2 `src/preprocessing/schema_graph_builder.py`：替换 `chat_json` → 用 `invoke(msgs, as_json=True)`（离线场景用 invoke）
- [x] 4.7.3 跑 `pytest tests/preprocessing -x` 通过

### 4.8 业务调用全部迁移完成

- [x] 4.8.1 全局 grep 确认无残留：`grep -rn "\.chat_json\|\.chat_stream\|self\.client\." src/` 输出为空
- [x] 4.8.2 全局 grep 确认无 dict messages：`grep -rn '\[{"role":' src/` 输出为空
- [x] 4.8.3 跑全量 `pytest -x` 通过
- [ ] 4.8.4 → commit 4：`refactor(llm): 13+ 处业务调用全量迁移到 invoke/stream，prompt 全模板化`

## 5. Step 5 — 测试改造 + 真实回归

- [x] 5.1 重写 `tests/api/test_streaming.py` 的 `_make_chunk` 辅助函数：用 `from langchain_core.messages import AIMessageChunk` 构造真实 chunk
- [x] 5.2 重写 `_build_llm_client` 后续的 mock：`client._chat_model.invoke = MagicMock(return_value=AIMessage(content='{"k":1}'))` / `client._chat_model.stream = MagicMock(return_value=iter([...]))`
- [x] 5.3 测试用例验证 `stream_with_sse` 的 SSE 推送正确（用 ContextVar 设置 emitter）
- [x] 5.4 业务测试批量替换：`grep -rln "chat_json" tests/` → 把 `mock_llm.chat_json.return_value = ...` 改为 `mock_llm.invoke.return_value = ...`
- [x] 5.5 同上：把 `mock_llm.chat_json.side_effect = ...` 改为 `mock_llm.invoke.side_effect = ...`
- [x] 5.6 跑 `pytest -x` 全量通过
- [x] 5.7 启动 API 服务（`uvicorn src.api.app:app` 或现有启动命令），发一条真实 NL2SQL query
- [x] 5.8 在浏览器 / curl 验证 SSE 流：`llm_thinking` 事件中文输出正常，`stage` / `score_r1` / `final_decision` 事件流不中断
- [x] 5.9 切 `LLM_CHINESE_THINKING=false` 跑一遍 query，验证英文思考链照常返回
- [x] 5.10 执行一次离线脚本（如 `python -m src.preprocessing.build_schema_graphs` 或类似），验证 `invoke` 路径无报错
- [x] 5.11 `git rm scripts/probe_chatopenai_reasoning.py`
- [x] 5.12 → commit 5：`test: LangChain mock 改造完成 + 真实回归通过 + 删除探测脚本`

## 6. 文档与归档

- [x] 6.4 全量验证通过后执行 `openspec archive migrate-llm-client-to-langchain`

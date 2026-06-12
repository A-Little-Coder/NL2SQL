## Why

当前 LLMClient 的 `enable_thinking` 是构造时全局配置，所有 LLM 调用要么全开 thinking、要么全关，无法按步骤灵活切换。但管线中 13 个 LLM 调用点的推理深度差异很大：SQL 生成必须 thinking，而关键词抽取、JOIN 推断、可回答性检查等简单任务开 thinking 纯属浪费 token 和延迟。同时，旧 `llm_final_decision` 方法已被决策 51 的两段式评分取代，但代码残留未清理。

## What Changes

- **新增** LLMClient 四个公开方法（invoke / stream / ainvoke / astream）的 `thinking: Optional[bool]` 参数，允许每次调用独立覆盖构造时的 `enable_thinking` 默认值
- **新增** `_bind_runtime` 方法的 `thinking` 参数，通过 `bind(extra_body={"enable_thinking": ...})` 实现按调用切换
- **关闭 thinking** 3 个简单步骤：关键词抽取、JOIN 推断、可回答性检查
- **删除** `llm_final_decision()` 方法及其依赖的旧决策路径（`decide()`、`group_by_result()`、`find_majority_group()`、`select_fastest_from_group()`）
- **删除** `LLM_FINAL_DECISION_PROMPT` 模板定义

## Capabilities

### New Capabilities

（无新增 capability——`thinking` 参数是 llm-client spec 的扩展）

### Modified Capabilities

- `llm-client`: 新增 per-call `thinking` 参数，允许业务调用按步骤覆盖全局 `enable_thinking`；删除 `llm_final_decision` 相关残留

## Impact

- `utils/llm_client.py`：公开 API 签名变更（新增 `thinking` 参数，向后兼容默认 None）
- `src/decision/self_consistency.py`：删除 ~140 行旧决策代码
- `src/decision/prompts.py`：删除 `LLM_FINAL_DECISION_PROMPT`
- `src/retrieval/information_retrieval.py`：LLM 调用加 `thinking=False`
- `src/preprocessing/schema_graph_builder.py`：LLM 调用加 `thinking=False`
- `src/verification/answerability.py`：LLM 调用加 `thinking=False`
- 相关测试文件需适配

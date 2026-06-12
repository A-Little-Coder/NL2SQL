## Context

LLMClient 当前 `enable_thinking` 在 `__init__` 构造时写入 `extra_body={"enable_thinking": True/False}`，整个实例生命周期不可变。管线中 13 个 LLM 调用点的推理深度差异大：SQL 生成必须 thinking，关键词抽取等简单任务开 thinking 白花 200-500 reasoning tokens + 延迟。

同时，决策 51 的两段式评分（R1/R2 + SmartFix）已取代旧的 `llm_final_decision` 路径，但 `self_consistency.py` 中 `decide()` + `llm_final_decision()` + 辅助方法 + `LLM_FINAL_DECISION_PROMPT` 仍残留，新子图 `decision_graph.py` 完全不走这些代码。

## Goals / Non-Goals

**Goals:**
- 给 LLMClient 四个公开方法加 `thinking: Optional[bool]` 参数，按调用覆盖全局 `enable_thinking`
- 对 3 个简单步骤（关键词抽取、JOIN 推断、可回答性检查）显式关闭 thinking
- 清理 `llm_final_decision` 及其依赖的旧决策代码残留

**Non-Goals:**
- 不对"可关可留"的 3 个步骤（列评分、结果验证、R1 评分）做调整——后续按需
- 不改离线 memory 模块的 thinking 设置
- 不改 `ChatPromptTemplate` 或 prompt 内容
- 不做 LangSmith 集成（另议）

## Decisions

### 决策 1：per-call thinking 通过 `bind(extra_body=...)` 实现

**方案**：在 `_bind_runtime` 中，当 `thinking` 参数非 None 时，通过 `self._chat_model.bind(extra_body={"enable_thinking": thinking_value})` 覆盖构造时的 `extra_body`。

**理由**：LangChain `Runnable.bind()` 的 kwargs 在调用时合并到请求参数中，`extra_body` 会被覆盖。这比构造两个 ChatOpenAI 实例更轻量，也不破坏现有的 mock 锚点（`patch.object(client, "_bind_runtime")`）。

**备选**：
- 方案 B：构造两个 ChatOpenAI 实例（一个 thinking on，一个 off），按需选择 → 增加内存和初始化成本，mock 复杂度翻倍
- 方案 C：每次调用临时创建新 ChatOpenAI → 极度浪费，不现实

### 决策 2：`thinking=None` 表示沿用构造时默认值

**理由**：向后兼容。现有所有业务调用不传 `thinking`，行为不变。只有需要覆盖时才显式传 `thinking=True/False`。

### 决策 3：关闭 thinking 的 3 个步骤

| 步骤 | 理由 |
|------|------|
| 关键词抽取 | 纯信息提取 + 同义词展开，不需要推理 |
| JOIN 推断 | 列名比对，模式固定，不需要推理 |
| 可回答性检查 | 三分类 + 宽松原则，不需要推理 |

### 决策 4：删除旧决策代码的范围

删除 `SelfConsistencyDecision` 中以下方法和它们的测试：
- `llm_final_decision()` — 已被 R1/R2 评分取代
- `decide()` — 旧入口，新子图用 `build_graph()`
- `group_by_result()` / `find_majority_group()` / `select_fastest_from_group()` — 只被 `decide()` 调用
- `LLM_FINAL_DECISION_PROMPT` — 只被 `llm_final_decision()` 引用

保留：`compute_result_hash()`（可能被其他地方引用）、`score_by_data()` / `score_by_sql()` / `_pick_from_scores()` / `pick_lightest_failures()` / `_format_candidate_data_preview()` / `_truncate_cell()` — 新子图仍在用。

## Risks / Trade-offs

- **`bind(extra_body=...)` 覆盖行为**：如果 LangChain 未来版本改变 `bind()` 对 `extra_body` 的合并策略，可能失效 → 用探测脚本验证后锁定版本；测试中增加对 `_bind_runtime` 输出的断言
- **前端 SSE 无思考链动画**：关闭 thinking 后 `stream_with_sse` 不推送 `llm_thinking` 事件 → 对关键词抽取等简单步骤用户本来也不关注思考过程，可接受
- **删除 `decide()` 后如果有外部调用者** → 全局 grep 确认无外部引用再删

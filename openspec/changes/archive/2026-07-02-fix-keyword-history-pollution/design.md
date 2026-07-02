## Context

NL2SQL 在 IR 阶段用 LLM 从用户查询提取关键词（`extract_keywords`）。当请求携带 `conversation_history` 时，走 `KEYWORD_EXTRACTION_WITH_HISTORY_PROMPT`，把最近 3 轮历史注入 prompt 辅助 follow-up 省略句理解（如"那去年的呢"→"苹果去年的销售额"）。

会话历史由 `src/api/routes/query.py` 在每次请求完成后写入 `session.add_turn`（反问 interrupt 挂起时跳过）。`get_recent_turns(n=5)` 在下次请求时取回，注入 `initial_state["conversation_history"]`。

观测到的问题：同 session 连发「帮我删库」(REJECT) → 「查询所有学校的平均sat成绩」，第二个请求的关键词被提取成「删库」。链路：请求 1 虽拒答仍写入会话 → 请求 2 取回该历史 → IR 注入历史 → qwen3.6-flash 从无关历史提词。

约束：
- 不能破坏真正 follow-up 省略句的历史补全能力（核心价值）。
- `extract_keywords` 代码结构稳定，本次只改 prompt 文本，不动调用逻辑。
- 会话记忆写入语义变更需谨慎，确认不影响 task_planner / memory_updater 等其他历史消费方。

## Goals / Non-Goals

**Goals:**
- 自足查询（实体/度量/时间齐全，非省略句）的关键词提取完全不受无关会话历史影响。
- 被拒答或未产出最终 SQL 的请求不写入会话历史，从源头消除无意义历史。

**Non-Goals:**
- 不改 `extract_keywords` 的代码结构、LSH/向量召回逻辑。
- 不改 follow-up 省略句的历史补全行为（"那去年的呢"仍应结合历史）。
- 不引入"是否 follow-up"的启发式判断（方案 C）——完全靠 prompt 约束 + 写入语义治理。
- 不改 `single_query_graph` 编排（与 refactor-single-query-graph 解耦）。

## Decisions

### 决策 1：A 方案——用 prompt 约束隔离无关历史（止血）
在 `KEYWORD_EXTRACTION_WITH_HISTORY_PROMPT` 末尾的"注意"段强化约束，并补反例：
- 明确"历史仅用于补全省略句中缺失的指代（如代词、省略的实体）"。
- 明确"若当前查询已自足（含完整实体、度量、时间），则完全忽略历史，只从当前查询提取关键词"。
- 反例：历史 `["帮我删库"]`，当前 `"查询所有学校的平均sat成绩"` → 只提「sat成绩」「学校」，不提「删库」。

**备选**：方案 C（启发式判断是否 follow-up 再决定注入历史）。否决——增加复杂度且启发式易误判；prompt 约束 + 写入语义治理更稳。

### 决策 2：B 方案——拒答/无 SQL 不入会话（治本）
在 `query.py:263` 的 `if not accumulated.get("__interrupted__")` 写入分支内，增加条件：仅当**产出了 final_sql**（`accumulated.get("final_sql")` 非空）才写 `add_turn`。拒答（`rejection_reason`）和无 SQL 失败路径天然无 final_sql，被一并拦截。

**备选**：显式判断 `rejection_reason` 或 `decision_path in (FAILED/REJECTED)`。否决——`final_sql` 非空是"该轮产出了可用 SQL"的统一判据，更简洁，且覆盖所有失败路径（fail-fast 早退、SmartFix 失败等）。

### 决策 3：B 方案不影响其他历史消费方
- `task_planner.plan()` 接收 `conversation_history` 用于 follow-up 理解——拒答轮本就无 SQL，对 follow-up 无指代价值，不入会话反而更干净。
- `memory_updater` 依赖成功轮次的 SQL/结果学习用户记忆——无 SQL 轮次本就无可学内容。
- `history_cache` 召回历史 SQL——无 SQL 轮次本就无可复用 SQL。
结论：拦截无 SQL 轮次对所有消费方均为正向或中性，无负面影响。

## Risks / Trade-offs

- **[prompt 约束仍可能被弱模型绕过]** qwen3.6-flash 等小模型可能不完全遵守"忽略历史"指令。
  → 缓解：B 方案从源头减少无关历史（拒答/失败不入会话），双重保险；A 方案 prompt 反例强化约束。两方案组合后，剩余风险仅为"成功但语义无关的历史轮次"（如先查 A 表再查 B 表），属合理的 follow-up 语义边界，不在本次修复范围。
- **[会话记忆写入语义变更]** 拒答/失败轮次不再入会话，可能影响"用户问过什么"的审计需求。
  → 缓解：拒答/失败信息仍通过 SSE `done` 事件返回前端，前端可自行记录；会话记忆定位是"follow-up 上下文 + 历史SQL复用"，非审计日志，语义本就该排除无结果轮次。
- **[测试依赖真实 LLM]** A 方案的 prompt 效果需 LLM 验证。
  → 缓解：用 mock LLM 返回固定 JSON 验证 prompt 模板渲染正确；真实 LLM 行为靠 e2e 手测（同 session 连发两请求验证）。

## Migration Plan

1. 改 `src/retrieval/prompts.py` 的 `KEYWORD_EXTRACTION_WITH_HISTORY_PROMPT`（A）。
2. 改 `src/api/routes/query.py` 的 add_turn 写入条件（B）。
3. 新增 `tests/retrieval/` 关键词提取历史隔离单测（mock LLM）。
4. 新增 `tests/api/` 会话写入语义单测（拒答/无 SQL 不入会话）。
5. e2e 手测：同 session 连发「帮我删库」→「查询所有学校的平均sat成绩」，验证第二个请求关键词为 sat/学校。

**回滚**：两个文件独立改动，单一 commit 回滚。无数据迁移。已写入会话的旧无结果轮次无需清理（下次请求自然取最近 5 轮，旧轮次会被挤出）。

## Open Questions

- 无。两方案均在小范围、低风险，可直接开发。

## Why

在同一会话内连发两个请求时，第二个请求的关键词提取会被第一个请求的查询内容污染：例如先发「帮我删库」（被 TaskPlanner 拒答、未产出 SQL），再发「查询所有学校的平均sat成绩」，IR 的 `extract_keywords` 却从会话历史里提取出「删库」而非「sat成绩」，导致后续 schema 召回完全跑偏。

根因有二：
1. **关键词提取 prompt 被历史带偏**：`KEYWORD_EXTRACTION_WITH_HISTORY_PROMPT` 要求"结合历史查询理解当前查询完整意图"，但当前查询本身自足（非省略句 follow-up）时，LLM 仍从无关历史中提取关键词。
2. **无意义轮次被写入会话历史**：被拒答（REJECT）或未产出 SQL 的请求仍经 `session.add_turn` 写入会话（`src/api/routes/query.py`），这类无结果轮次进入 `conversation_history`，污染后续 follow-up 理解。

## What Changes

- **A（止血）**：修改 `src/retrieval/prompts.py` 的 `KEYWORD_EXTRACTION_WITH_HISTORY_PROMPT`，明确约束"会话历史仅用于补全省略句（follow-up）的指代；当前查询若自足（实体/度量/时间齐全），完全忽略历史"，并补充"自足句 + 无关历史"的反例（应只提当前查询的关键词）。
- **B（治本）**：修改 `src/api/routes/query.py` 的会话历史写入逻辑——被拒答（`rejection_reason` 非空）或未产出最终 SQL（`final_sql` 为空）的请求**不写入** `session.add_turn`，从源头消除无意义历史对后续 follow-up 的污染。
- 新增测试：关键词提取在"有无关历史"时仍只提当前查询关键词；拒答/无 SQL 请求不入会话历史。

## Capabilities

### New Capabilities
- `keyword-extraction-history-isolation`: 关键词提取与会话历史的隔离规则。定义 `KEYWORD_EXTRACTION_WITH_HISTORY_PROMPT` 的行为契约：历史仅用于补全省略句指代，自足查询忽略历史。

### Modified Capabilities
- `session-memory-write-semantics`: 会话记忆写入语义。原本所有非 interrupt 请求均写入会话；现变更拒答/无 SQL 请求不写入，避免污染后续 follow-up。（无现存主 spec，作为本 change 内新增 spec 定义）

## Impact

- 修改文件：`src/retrieval/prompts.py`（prompt 文本）、`src/api/routes/query.py`（add_turn 拦截条件）。
- 新增测试：`tests/retrieval/` 下关键词提取历史隔离测试；`tests/api/` 下会话写入语义测试。
- 不影响：IR 召回/LSH/向量检索逻辑、`extract_keywords` 的代码结构、真正 follow-up 省略句（如"那去年的呢"）的历史补全能力、`single_query_graph` 编排（与 refactor-single-query-graph 解耦）。
- 风险：B 方案改变了会话记忆写入条件，需确认拒答/失败轮次不入会话不影响其他依赖 `conversation_history` 的逻辑（如 task_planner 的 follow-up 理解、memory_updater）——这些逻辑本就该依赖成功轮次。

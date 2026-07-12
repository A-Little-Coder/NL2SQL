## REMOVED Requirements

### Requirement: Keyword Extraction Ignores Irrelevant History
**Reason**: 关键词提取不再依赖会话历史做隐式指代消解。该能力全部移至 Rewrite 环节（`query-rewrite` spec），在 history_cache 之前完成改写。IR 关键词提取只接收改写后的自足 query，不再需要 `conversation_history` 参数。

**Migration**: 
- 删除 `KEYWORD_EXTRACTION_WITH_HISTORY_PROMPT`
- 删除 `information_retrieval.py` 中 `extract_keywords()` 的 `conversation_history` 参数
- 删除 `ir_graph.py` 中 `conversation_history` 字段的传递
- IR 关键词提取始终使用 `KEYWORD_EXTRACTION_PROMPT`（无历史版）

#### Scenario: 删除历史 prompt 切换
- **WHEN** IR 阶段的 `extract_keywords` 被调用
- **THEN** 始终使用 `KEYWORD_EXTRACTION_PROMPT`，不再检查 `conversation_history` 是否为空

### Requirement: Prompt Template Enforces Isolation Rule
**Reason**: `KEYWORD_EXTRACTION_WITH_HISTORY_PROMPT` 已被删除，不再需要隔离规则约束。

**Migration**: 删除 `src/retrieval/prompts.py` 中的 `KEYWORD_EXTRACTION_WITH_HISTORY_PROMPT`。
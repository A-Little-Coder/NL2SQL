# Keyword Extraction History Isolation Specification

## Purpose

关键词提取与会话历史的隔离规则。当 IR 阶段的 `extract_keywords` 使用
`KEYWORD_EXTRACTION_WITH_HISTORY_PROMPT` 时，会话历史仅用于补全省略句（follow-up）
中缺失的指代；自足查询（含完整实体/度量/时间）应完全忽略历史，避免无关历史轮次
（如被拒答的"帮我删库"）污染当前查询的关键词提取。

## Requirements

### Requirement: Keyword Extraction Ignores Irrelevant History
当 `extract_keywords` 使用 `KEYWORD_EXTRACTION_WITH_HISTORY_PROMPT` 时，会话历史 SHALL 仅用于补全省略句（follow-up）中缺失的指代（代词、省略实体）。当前查询若自足（含完整实体、度量、时间表达式），关键词提取 SHALL 完全忽略会话历史，只从当前查询提取关键词。

#### Scenario: 自足查询忽略无关历史
- **WHEN** 会话历史为 `["帮我删库"]` 且当前查询为 `"查询所有学校的平均sat成绩"`（自足句）
- **THEN** 关键词提取结果 SHALL 只包含与当前查询相关的词（如「sat成绩」「学校」），SHALL NOT 包含历史中的「删库」

#### Scenario: 省略句 follow-up 仍结合历史
- **WHEN** 会话历史为 `["查询苹果的销售额"]` 且当前查询为 `"那去年的呢"`（省略句）
- **THEN** 关键词提取 SHALL 结合历史补全指代，结果包含「苹果」「去年」「销售额」

#### Scenario: 无历史时走无历史 prompt
- **WHEN** `conversation_history` 为空或无有效历史
- **THEN** `extract_keywords` SHALL 使用 `KEYWORD_EXTRACTION_PROMPT`（无历史版），行为不变

### Requirement: Prompt Template Enforces Isolation Rule
`KEYWORD_EXTRACTION_WITH_HISTORY_PROMPT` 的 prompt 文本 SHALL 明确包含"历史仅用于补全省略句指代、自足查询忽略历史"的约束，并包含至少一个"自足句 + 无关历史"的反例。

#### Scenario: prompt 含隔离约束与反例
- **WHEN** 渲染 `KEYWORD_EXTRACTION_WITH_HISTORY_PROMPT`
- **THEN** prompt 文本 SHALL 包含约束语句（历史仅补全指代）与反例（自足句忽略历史，不提历史关键词）

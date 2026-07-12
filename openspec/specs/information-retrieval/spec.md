## ADDED Requirements

### Requirement: 值检索结果标注来源关键词组

`retrieve_values` SHALL 在每个召回的 `RetrievedItem.metadata` 中记录 `source_phrase`（命中的关键词组 phrase）与 `source_term`（具体命中的 term），使值召回结果具备准确的关键词组归属，而非扁平化丢失。当一个 value 被多个 term 命中时，系统 MUST 按 LSH 相似度最高的 term 所属 phrase 归属。

#### Scenario: 值召回携带来源组
- **WHEN** IR 对查询"各科score和学校总数"提取两个关键词组并执行值检索
- **THEN** 每个 `RetrievedItem` 的 `metadata` 含 `source_phrase`（如"各科score"或"学校总数"）与 `source_term`（具体命中的同义词）
- **AND** 前端可据 `source_phrase` 将值准确归到对应关键词组下展示

#### Scenario: 多 term 命中同一 value 按最高分归属
- **WHEN** 同一 value 被分属不同 phrase 的多个 term 命中
- **THEN** 该 value 的 `source_phrase` 归属到 LSH `jaccard_score` 最高的 term 所在 phrase
- **AND** 不出现一个 value 同时归多组的情况

#### Scenario: 无组命中的 value 仍保留
- **WHEN** 某 value 被扁平 terms 命中但其所属 phrase 无法确定（极端边界）
- **THEN** 该 value 的 `source_phrase` 置空字符串
- **AND** 前端将其归到"未归属"分组展示，不丢失

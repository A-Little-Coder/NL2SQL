## ADDED Requirements

### Requirement: SS 未选出表时显式拒答

`schema_finalize`（或 SS）节点检测到 `selected_schema` 为空时 SHALL 设置 `rejection_reason`（友好提示，如"未在数据库中找到与查询相关的表或字段，请尝试换一种表述或确认数据范围"）并 emit `schema_empty` SSE 事件（携带 `reason`），随后路由到 END。MUST NOT 静默 END——即不得在未设置 `rejection_reason` 且未 emit `schema_empty` 的情况下直接结束流水线。

#### Scenario: 未选出表时显式拒答
- **WHEN** SS / schema_finalize 产出 `selected_schema=[]`
- **THEN** state 设置 `rejection_reason` 为友好提示
- **AND** emit `schema_empty` 事件（reason 同 rejection_reason）
- **AND** 流水线路由到 END，不进入 answerability_check / cg

#### Scenario: 选出表时正常放行
- **WHEN** `selected_schema` 非空
- **THEN** 不 emit `schema_empty`，不设拒答 reason，正常进入 answerability_check / cg

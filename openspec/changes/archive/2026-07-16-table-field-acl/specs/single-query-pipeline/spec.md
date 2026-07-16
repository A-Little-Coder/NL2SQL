## ADDED Requirements

### Requirement: 权限检查节点接入流水线
系统 SHALL 在 single_query_pipeline 的 SS 之后、schema_finalize 之前接入权限检查节点。权限节点 SHALL 读取 selected_schema 与 retrieved_context 的关键词-字段映射做权限判断，输出裁剪后的 selected_schema 或触发 permission_choice 反问。

#### Scenario: 权限节点位于 SS 之后
- **WHEN** SS 产出 selected_schema
- **THEN** 流水线先经权限节点裁剪或反问，再进入 schema_finalize

### Requirement: 结果脱敏节点接入流水线
系统 SHALL 在 execution 之后接入脱敏节点，对最终执行结果按黑名单字段脱敏。脱敏节点 SHALL 对主路径与 cache_hit 短路路径均生效。

#### Scenario: 脱敏节点覆盖 cache 短路路径
- **WHEN** cache_hit 为真跳过 ir/ss/cg 直奔 execution
- **THEN** execution 后仍经脱敏节点对黑名单字段脱敏

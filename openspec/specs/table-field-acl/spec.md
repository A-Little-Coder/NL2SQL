# table-field-acl Specification

## Purpose
TBD - created by archiving change table-field-acl. Update Purpose after archive.
## Requirements
### Requirement: RBAC 黑名单权限模型
系统 SHALL 采用基于角色的访问控制（RBAC）与黑名单策略：员工挂一个或多个角色，角色绑定 deny_rules（禁止访问的表/字段），未在黑名单中的表/字段默认可访问。员工持多角色时，有效黑名单 SHALL 为各角色 deny_rules 的并集（黑名单只禁不放开，并集即最严）。

#### Scenario: 多角色黑名单取并集
- **WHEN** 员工 E 同时挂角色 R1（黑含 employees.salary）与 R2（黑含 employees.phone）
- **THEN** E 的有效黑名单为 {employees.salary, employees.phone}，两个字段均不可访问

#### Scenario: 未配置黑名单的表默认可访问
- **WHEN** 角色 R 的黑名单不含 orders 表任何字段
- **THEN** 挂角色 R 的员工可访问 orders 表全部字段

### Requirement: 权限节点关键词级判断与裁剪
系统 SHALL 在 SS 之后以关键词为单元对召回字段做权限判断：某关键词召回字段集存在可用字段（部分无权）时，SHALL 剔除无权限字段、保留可用字段继续往后走且不脱敏；剔除后某表无剩余字段时，SHALL 将该表从 selected_schema 移除。

#### Scenario: 部分无权字段被剔除
- **WHEN** 关键词"姓名"召回 [name(有权), full_name(无权)]
- **THEN** full_name 被剔除，name 保留，后续 final_sql 不含 full_name 且不脱敏

#### Scenario: 剔除后空表被移除
- **WHEN** 某表所有字段均无权限且该表无其他可用字段
- **THEN** 该表从 selected_schema 移除，不参与 join_paths 计算与 CG

### Requirement: 全无权关键词触发脱敏反问
系统 SHALL 在**任一**关键词召回字段全部无权限时（无需所有关键词均无权，只要存在一个全无权关键词组即可），对整条 query 通过 interrupt 发起 permission_choice 反问，提供"脱敏继续"与"放弃"两个选项。用户选"脱敏继续"时，SHALL 保留该全无权关键词字段参与 SQL 生成并在执行后脱敏（其余部分无权关键词字段仍剔除）；选"放弃"时，SHALL 拒答整条 query 并提示无权限字段。全无权判断 SHALL 基于 `retrieved_context.keyword_columns_map` 与 `selected_schema` 的交集按关键词分组，**不得**用「selected_schema 剔除后全空」作为全无权判据。**若某关键词在 SS 后无任何字段进入 `selected_schema`（交集为空），SHALL 跳过该关键词、不触发反问**（SS 已判定其字段与本次查询无关）。

#### Scenario: 全无权选择脱敏继续
- **WHEN** 关键词"薪资"召回字段全部无权限，用户点选"脱敏继续"
- **THEN** 薪资字段保留参与 SQL 生成，执行结果中薪资列被脱敏为 ***

#### Scenario: 全无权选择放弃
- **WHEN** 关键词"薪资"召回字段全部无权限，用户点选"放弃"
- **THEN** 查询被拒答，返回"薪资匹配的字段无权限"提示

#### Scenario: 任一关键词全无权即反问（核心语义）
- **WHEN** 用户问"姓名和薪资"，关键词"姓名"召回 name（有权），关键词"薪资"召回 salary（全无权）
- **THEN** 系统 SHALL 反问（而非静默剔除 salary）；选脱敏时结果含 name 真值与 salary ***，选放弃时整条拒答

#### Scenario: SS 后无召回字段的关键词不触发反问
- **WHEN** 关键词 K 在 IR 阶段召回过字段，但 SS 后这些字段均未进入 selected_schema（交集为空）
- **THEN** 系统 SHALL 跳过关键词 K，不因其触发全无权反问；仅当存在另一个交集非空且全无权的关键词时才反问

### Requirement: 部分剔除后下游自然判断
系统 SHALL 在权限节点部分剔除无权字段后，记录被拦截字段于 `acl_removed_fields`（供 trace_log 与前台展示），**不**新增 query_rewrite 节点改写 `user_query`，**不**向 answerability / result_verifier 注入 `acl_removed_note`。下游 answerability_check / CG / decision SHALL 直接使用 `user_query` + 剔除后 `selected_schema` 自然判断。若因被剔字段为核心、关键词-字段对应度低导致 answerability 拒答，SHALL 视为合理结果。`acl_removed_fields` SHALL 在 `NL2SQLState` 显式声明；`effective_query` 不再使用。

#### Scenario: 部分剔除后下游自然判断
- **WHEN** 用户问"学校的 School 和经度"，经度被部分剔除，acl_removed_fields=["schools.Longitude"]，schema 仅剩 School
- **THEN** 不改写 query，answerability 基于 user_query + 剩余 schema 自然判断：放行则 CG 生成 SELECT School；若判经度为核心、对应度低则拒答，二者均合理

#### Scenario: 全无权脱敏路径不改写不剔除
- **WHEN** 用户问"薪资"全无权并选"脱敏继续"
- **THEN** acl_removed_fields 不设，字段保留参与生成，末尾脱敏节点脱敏，下游自然放行

### Requirement: 执行结果脱敏后处理
系统 SHALL 在 execution 之后对最终 SQL 涉及的黑名单字段做脱敏：解析 final_sql 识别涉及字段（含聚合函数内字段与列别名），命中黑名单的结果列 SHALL 统一替换为 ***。脱敏判断 SHALL 基于 final_sql 自身，不依赖权限节点传递标记。

#### Scenario: 普通字段脱敏
- **WHEN** final_sql 为 SELECT name, salary FROM employees 且 salary 在黑名单
- **THEN** 结果中 salary 列替换为 ***，name 列保持原值

#### Scenario: 聚合字段脱敏
- **WHEN** final_sql 为 SELECT dept, AVG(salary) FROM employees GROUP BY dept 且 salary 在黑名单
- **THEN** AVG(salary) 结果列替换为 ***，dept 列保持原值

#### Scenario: 别名字段脱敏
- **WHEN** final_sql 为 SELECT salary AS s FROM employees 且 salary 在黑名单
- **THEN** 别名列 s 的结果替换为 ***

#### Scenario: cache 路径同样脱敏
- **WHEN** cache_hit 命中且 cached_sql 含黑名单字段
- **THEN** 执行结果中黑名单字段列被脱敏，无需权限节点参与

### Requirement: 表级黑名单展开为字段级
系统 SHALL 将表级黑名单规则展开为"该表所有字段无权限"，与字段级黑名单统一走脱敏路径。字段 (T,C) 被判定无权限当且仅当存在规则使 table_pattern 匹配 T 且 column_pattern 缺省或匹配 C。

#### Scenario: 表级黑名单展开
- **WHEN** 角色 R 黑名单含 audit_log 表级规则（column_pattern 缺省）
- **THEN** audit_log 表所有字段对该角色无权限，查询该表时全部字段脱敏或触发全无权反问

### Requirement: 权限策略元数据存储
系统 SHALL 在 auth/table_field_acl.db 独立元数据库中存储 roles、users、user_roles、deny_rules 表，deny_rules 携带 db_id 维度适配多库架构，column_pattern 缺省表示整表禁。系统 SHALL 预留 auth/field_value_acl.db 供将来字段值/行级权限。

#### Scenario: 多库策略隔离
- **WHEN** 同一角色在 db_id=sales 与 db_id=hr 配置不同黑名单
- **THEN** 该角色员工在两库下分别适用各自黑名单

### Requirement: 权限管理后台与 REST API
系统 SHALL 提供独立 /admin 后台（共用 frontend 工程）与 REST API，支持员工/角色 CRUD、表/字段黑名单配置（通配模式），以及查询当前用户有效权限。

#### Scenario: 后台配置黑名单后前台即时生效
- **WHEN** 管理员在 /admin 给角色 R 新增 employees.salary 黑名单并绑定员工 E
- **THEN** 员工 E 前台查询薪资时触发脱敏或全无权反问


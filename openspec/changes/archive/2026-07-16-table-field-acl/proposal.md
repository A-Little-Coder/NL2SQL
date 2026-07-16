## Why

NL2SQL 当前的 `user_id` 仅用于个性化记忆（术语偏好/常用表/指标），**没有任何权限体系**。LLM 基于召回的全部 schema 生成 SQL，任何用户都能查到敏感表/字段（薪资、手机号、身份证等）。需要在不破坏问数体验的前提下，对员工按表/字段粒度做访问控制：敏感字段默认脱敏而非直接拒答，仅当用户查询完全命中敏感字段时反问确认，兼顾安全与可用。

## What Changes

- **新增 RBAC 权限模型**：员工-角色-黑名单规则。黑名单存"禁止访问的表/字段"，其余默认放行；多角色取并集（黑名单只禁不放开，并集即最严）。
- **新增权限元数据库** `auth/table_field_acl.db`（独立于业务库，带 `db_id` 维度适配多库架构），预留 `auth/field_value_acl.db` 供将来字段值/行级权限。
- **SS 之后插入权限节点（关键词级判断）**：回捞 `retrieved_context.keyword_columns_map`（phrase→字段）与 `selected_schema` 取交集，**按关键词分组**判断。**任一关键词**召回字段全无权 → 触发反问（核心语义：只要有一个关键词组全无权即反问，**不是**「所有关键词所有字段都没权限」才反问）；所有关键词均仅部分无权 → 剔除无权字段、保留可用字段往后走（不脱敏）。SS 后无字段进入 `selected_schema` 的关键词跳过、不触发反问（SS 已判定无关）。部分剔除时权限节点记录被拦截字段（`acl_removed_fields`，前台展示），**不再改写 query**：下游 answerability/CG/verifier 基于 `user_query` + 剔除后 schema 自然判断，若因关键词-字段对应度低而拒答属合理结果。
- **改造 single_query_graph 为 subgraph 编译进 main_graph（方案 B）**：权限节点在子图内 SS 之后 interrupt，被主图 checkpointer 接管，一次跑完，避免两阶段重跑；废弃 `make_run_single_query_node` 的 invoke 手动调用。
- **复用现有 interrupt/resume 反问机制**：全无权时反问"是否脱敏继续"，提供"脱敏继续/放弃"tag 选择；选脱敏则字段保留参与 SQL 生成。
- **多意图路径权限降级**：`SubqueryOrchestrator` 仍 invoke 子图、不支持子查询级 interrupt，多意图子查询全无权时直接拒答该子查询（不反问）；仅单意图路径支持反问脱敏。
- **新增脱敏节点（execution 之后）**：解析 `final_sql` 涉及字段（含聚合、别名），黑名单命中列统一替换为 `***`；不依赖权限节点传标记，cache 路径自动覆盖。
- **表级黑名单展开**为"该表所有字段无权限"，统一走字段级脱敏路径。
- **cache 路径隔离**：向量检索带 `user_id` metadata 过滤，保证 A 的历史 SQL 不被 B 复用；脱敏节点对 cache 路径同样生效。
- **新增后台管理前端**（`/admin`，共用 `frontend/` 工程）：员工/角色 CRUD、表/字段黑名单配置（通配模式）。
- **新增权限管理 REST API**：策略 CRUD + 当前用户有效权限查询。

### 端到端测试方案（Playwright，依 CLAUDE.md 第 9 条）

针对 `frontend/` 工程编写，覆盖五条核心链路：

1. **部分无权剔除 + 下游自然判断**：用户问“姓名和部门”，部门字段无权限→权限节点剔除部门并记录拦截字段→下游 answerability 基于 user_query + 剩余 schema 自然判断（放行则结果只含姓名真值；若部门为核心、对应度低则拒答，均合理），无脱敏、无反问。
2. **全无权反问脱敏**：用户问"所有人薪资"，薪资全黑→弹反问 tag→点"脱敏继续"→结果薪资列为 `***`；点"放弃"→拒答提示。
3. **任一关键词全无权即反问（核心语义）**：用户问"姓名和薪资"，姓名有权、薪资全无权 -> **反问**（而非静默剔除薪资）；选脱敏 -> 姓名真值+薪资 `***`；选放弃 -> 整条拒答。
4. **后台配置生效**：在 `/admin` 给某角色新增一条字段黑名单→该角色用户再次问该字段→触发脱敏/反问。
5. **cache 跨用户隔离**：用户 A 问出某 SQL，用户 B 问相似问题→不命中 A 的缓存（向量检索 user_id 过滤）。

后台 CRUD 页面用 Playwright 走"新增角色→配置黑名单→绑定员工→前台验证生效"全流程。

## Capabilities

### New Capabilities

- `table-field-acl`: 表和字段权限管理能力。覆盖 RBAC 黑名单模型、auth 元数据存储、权限节点的关键词级判断逻辑（任一关键词组全无权即反问）、部分剔除拦截字段记录、脱敏节点的 SQL 解析脱敏、后台管理与 REST API。

### Modified Capabilities

- `single-query-pipeline`: 在 SS 之后插入权限节点、execution 之后插入脱敏节点，改变流水线节点拓扑与早退/反问分支。
- `clarification-interaction`: 反问机制扩展"权限脱敏选择"类型（`kind: permission_choice`），复用 interrupt/resume 与前端 tag UI。
- `execution-engine`: 执行结果增加脱敏后处理，对黑名单命中列（含聚合）掩码。
- `session-memory-hybrid-recall`: 向量检索增加 `user_id` metadata 过滤，实现历史复用的跨用户隔离。
- `frontend-ui`: 新增 `/admin` 权限管理后台路由与页面（员工/角色/黑名单 CRUD）。

## Impact

- **后端新增**：`src/permission/` 模块（权限节点、脱敏节点、`PolicyStore`、SQL 字段解析）、`auth/table_field_acl.db` 元数据、`src/api/routes/admin.py`。
- **后端修改**：`src/graph/single_query_graph.py`（插 permission / mask 节点）、`src/graph/main_graph.py`（**run_single_query 从 invoke 改为 subgraph 编译进主图，方案 B 核心改造**）、`src/graph/state.py`（新增 `acl_removed_fields` 字段）、`src/api/routes/query.py`（反问事件透传）、`src/memory/session_recall.py` / `history_cache.py`（user_id 过滤，已实现）、`src/api/db_pool.py`（DbContext 注入 PolicyStore）、`src/clarification/subquery_orchestrator.py`（多意图权限降级处理）。部分剔除后下游 answerability/CG/verifier 直接用 `user_query` + 剔除后 schema 自然判断，不注入 `acl_removed_note`、不改写 query。
- **前端新增**：`frontend/` 下 `/admin` 路由、角色/员工/黑名单管理页面、API client。
- **依赖新增**：`sqlglot`（脱敏节点解析 SQL 字段/聚合/别名）；后端无其他新依赖。
- **数据**：新建 `auth/table_field_acl.db`；现有业务库零改动；现有 `data/` 下业务 sqlite 不受影响。
- **测试**：`tests/` 下 pytest 单测（权限判断/脱敏/PolicyStore/SQL 解析）+ `frontend` Playwright E2E（上述四条链路）。
- **非目标**：字段值/行级权限（预留 `field_value_acl.db` 不实现）、真登录/token 认证（沿用可信前端传 `user_id`）、SQL 层脱敏（采用应用层脱敏）、安全护栏（真实值内存暂存/日志脱敏，本期不做）。

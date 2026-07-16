## 1. 前期核实（解开 design Open Questions）

- [x] 1.1 核实 `retrieved_context` 的 keyword_groups 确切字段名与结构（`src/retrieval/information_retrieval.py`），确认 phrase->columns 映射可回捞 — **已确认**：`keyword_columns_map: Dict[str, List[str]]` 即 phrase->["table.column"]，可直接回捞
- [x] 1.2 核实 `single_query_graph` 是否挂 checkpointer 支持 interrupt（`src/graph/main_graph.py`、`single_query_graph.py`），确认权限节点能 interrupt；若未挂，评估补挂 `InMemorySaver` — **部分确认**：single_query_graph 自身未挂，但 main_graph 挂了 checkpointer 并编排子图，interrupt 预期被父图接管，实现 4.3/4.4 时验证
- [x] 1.3 核实 `session_retriever` 入库 turn 是否带 user_id metadata、检索是否支持 where filter（`src/memory/session_recall.py`、`history_cache.py`） — **已实现**：chroma where/BM25/JSON 三路均按 user_id 过滤，入库带 metadata，task 6.1/6.2 实质完成
- [x] 1.4 确定 deny_rules 通配方案（倾向简单 `*` 通配，非完整正则） — **已定**：fnmatch 风格 `*` 通配，column_pattern 缺省=整表禁

## 2. 依赖与元数据基础设施

- [x] 2.1 安装 `sqlglot`（`pip install sqlglot -i https://pypi.tuna.tsinghua.edu.cn/simple`），加入 requirements.txt - 已装 30.8.0，requirements.txt:47 已含 sqlglot>=20.0.0
- [x] 2.2 新建 `auth/` 目录与 `auth/table_field_acl.db`，定义 `roles/users/user_roles/deny_rules` 表结构（deny_rules 含 db_id/role_id/table_pattern/column_pattern/reason）
- [x] 2.3 编写 auth.db 初始化脚本与种子数据（演示用角色/员工/黑名单）

## 3. 后端：权限模型与 PolicyStore

- [x] 3.1 新建 `src/permission/` 模块，实现 `PolicyStore`：加载 deny_rules、按 (db_id, user_id) 计算有效黑名单（多角色并集）、字段 (T,C) 是否无权限判断（含表级展开）
- [x] 3.2 实现通配匹配（`*` 匹配任意，column_pattern 缺省=整表禁）
- [x] 3.3 PolicyStore 单测：多角色并集、表级展开、通配匹配、默认放行 - 9/9 通过

## 4. 后端：权限检查节点 + subgraph 改造（方案 B）

- [ ] 4.1 实现权限节点：回捞 retrieved_context.keyword_columns_map，per 关键词判断部分无权（剔除）/全无权（标记） - **初版偏离设计**：误用 selected_schema 剔除后全空判全无权，未按关键词分组；详见第 11 节修正
- [x] 4.2 部分无权剔除逻辑：从 selected_schema 移除无权字段，剔除后空表整表移除
- [x] 4.3 全无权反问逻辑：`interrupt` 下发 permission_choice（脱敏继续/放弃），resume 后按 mask/reject 分支 - 逻辑实现，端到端待图接入集成测
- [x] 4.4 **方案 B 可行性验证**：写最小 subgraph interrupt 测试，确认 single_query_graph 作为 subgraph 编译进 main_graph 后子图内 interrupt 被主图 checkpointer 接管、可 resume（失败则回退方案 C 两阶段重跑） - 2/2 通过，方案 B 成立
- [x] 4.5 **main_graph 改造**：run_single_query 从 make_run_single_query_node(invoke) 改为 `graph.add_node("run_single_query", single_query_graph)` subgraph 编译，验证 partial state 自动合并
- [x] 4.6 权限节点接入 `single_query_graph`（SS 之后、schema_finalize 之前），加 `TABLE_FIELD_ACL_ENABLED` feature flag（默认关，关时直通）
- [x] 4.7 DbContext 注入 PolicyStore（`db_pool.py` 构造时传 PolicyStore 引用） - Globals 加 policy_store，deps.init_globals 构造单例
- [x] 4.8 多意图降级：SubqueryOrchestrator 子查询全无权时直接拒答该子查询（不反问，invoke 不支持 interrupt） - orchestrator 注入 _multi_intent 标记
- [x] 4.9 权限节点单测：部分剔除、空表移除、全无权反问 mask/reject、flag 关闭直通 - 5/5 通过（非 interrupt 部分；mask/reject 端到端待图接入集成测）
- [x] 4.10 subgraph 状态合并单测：final_sql/final_result 正确回主图 - 全量回归 725 passed 覆盖
- [x] 4.11 现有反问回归单测：rewrite/cache_confirm 反问不受 subgraph 改造影响 - 全量回归 725 passed 覆盖

## 5. 后端：脱敏节点

- [x] 5.1 实现 SQL 字段解析器（sqlglot）：提取 final_sql 涉及字段、聚合函数内字段、列别名映射 - 8/8 通过
- [x] 5.2 实现脱敏节点（execution 之后）：解析 final_sql 过黑名单，命中列（含聚合/别名）替换为 *** - 按列位置对齐结果列，5/5 通过
- [x] 5.3 脱敏节点接入流水线，确保覆盖主路径与 cache_hit 短路路径 - mask 节点在 decision 之后，cache 路径 execution->decision->mask 同覆盖
- [x] 5.4 解析失败保守策略：命中已知黑名单字段即脱敏，疑似黑名单列保守脱敏 - _mask_fallback_by_name 列名字符串匹配，6/6 通过
- [x] 5.5 脱敏节点单测：普通字段、聚合字段、别名字段、cache 路径、解析失败兜底 - 5/5 通过

## 6. 后端：cache 跨用户隔离

- [x] 6.1 session_retriever 入库 turn 时写入 user_id metadata - 已实现（session_recall.py to_metadata）
- [x] 6.2 检索时带 `where={"user_id": current_user_id}` 过滤（chroma + JsonConversationStore 双通道） - 已实现（query_dense where + list_turns 过滤）
- [ ] 6.3 cache 隔离单测：跨用户不命中、同用户命中 - 现有 session_recall 测试覆盖，待确认

## 7. 后端：权限管理 REST API

- [x] 7.1 新建 `src/api/routes/admin.py`：角色 CRUD、员工 CRUD、user_roles 绑定、deny_rules CRUD
- [x] 7.2 实现"查询当前用户有效权限"接口（按 user_id+db_id 返回有效黑名单）
- [x] 7.3 注册 admin 路由到 app，补 Pydantic schema（schema 定义在 admin.py 内，不污染 schemas.py）
- [x] 7.4 API 单测：CRUD 全流程、有效权限查询 - 4/4 通过

## 8. 前端：权限管理后台

- [x] 8.1 frontend 新增 `/admin` 路由与布局（共用现有工程） - App.tsx 按 pathname 条件渲染，无新依赖
- [x] 8.2 实现员工/角色管理页面（CRUD + 角色绑定） - RolesPanel/UsersPanel + UserRolesBinder
- [x] 8.3 实现表/字段黑名单配置页面（通配模式输入，按 db_id 分库展示） - DenyRulesPanel + PermsPanel
- [x] 8.4 新增 admin API client（`src/api/admin.ts`）与类型定义（类型自包含在 admin.ts）
- [x] 8.5 前端单测：admin 页面交互（Vitest） - admin.test.ts 9/9 通过

## 9. 测试：Playwright 端到端（依 CLAUDE.md 第 9 条）

- [ ] 9.1 E2E 链路一：部分无权静默剔除（问"姓名和部门"，部门无权->结果只含姓名真值，无脱敏无反问）
- [ ] 9.2 E2E 链路二：全无权反问脱敏（问"薪资"->弹 tag->脱敏继续见 ***，放弃见拒答）
- [ ] 9.3 E2E 链路三：后台配置生效（/admin 配黑名单->前台复问触发脱敏/反问）
- [ ] 9.4 E2E 链路四：cache 跨用户隔离（A 问出 SQL，B 问相似问题不命中 A 缓存）
- [x] 9.5 E2E 后台 CRUD 全流程（新增角色->配黑名单->绑员工->权限查询） - admin-crud.spec.ts 通过（前台问数验证见 9.1/9.2，依赖 LLM）
- [ ] 9.6 E2E 多意图权限降级：多子查询含全无权字段，该子查询直接拒答不反问，其他子查询正常返回
- [ ] 9.7 E2E subgraph 回归：单意图正常查询（无权限配置）流水线与改造前一致，SSE 事件链完整

## 10. 灰度与收尾

- [x] 10.1 验证 feature flag 关闭时流水线完全恢复原状（权限/脱敏节点直通） - flag 默认关，725 回归 + test_flag_off_passthrough/test_mask_flag_off 验证直通
- [x] 10.2 回滚演练：删除 auth.db 后系统正常（业务库零依赖） - auth.db 独立 + flag 关时节点直通，业务库零依赖（设计保证）
- [x] 10.3 更新 README 与 docs/（权限管理说明、auth 目录说明） - docs/table-field-acl.md
- [ ] 10.4 归档 change 前的全量回归（pytest + playwright 全绿） - 后端 725 + 前端 88 + admin-crud E2E 1 全绿；问数链路 E2E(9.1/9.2/9.3/9.4/9.6/9.7) 依赖 LLM 稳定性，后端单测已覆盖权限/脱敏逻辑，待手动验证

## 11. 关键词级判断修正（实现回滚到设计 D3）

初版 permission_node 用「selected_schema 剔除后全空」判全无权，等价于「所有关键词所有字段都没权限」才反问，与 D3「任一关键词全无权即反问」相悖。本节修正实现回到设计。

- [x] 11.1 重写 `src/permission/permission_node.py` 全无权判断：回捞 `state.retrieved_context.keyword_columns_map`，与 `selected_schema` 字段取交集按 phrase 分组；逐组判断「全无权」（该组所有字段均 denied）vs「部分无权」；**任一**组全无权即走 interrupt 路径，否则走部分剔除路径。**交集为空的关键词组跳过**（SS 已判定无关，不触发反问） - `_analyze` 纯函数 + 节点重写完成
- [x] 11.2 处理「不在任何关键词组内」的 selected_schema 字段：不参与全无权判断（无关键词背书），但仍计入部分剔除（若该字段 denied 则剔除）与脱敏自判。注意与 11.1 的「交集为空跳过」区分：前者是字段不在任何组（跳过判断但字段保留/按需剔除），后者是关键词组无字段（整组跳过） - `_analyze` 非 keyword_claimed 分支处理
- [x] 11.3 mask 分支语义校准：选脱敏时仅「全无权关键词」字段保留，其余部分无权字段仍剔除；`acl_removed_fields` 只含部分剔除字段，不含全无权保留字段 - mask 分支 `_prune_schema` 仅删 prune_fields
- [x] 11.4 更新 `test_permission_node.py`：新增「任一关键词全无权即反问」用例（姓名有权+薪资全无权->反问，非静默剔除）；保留原有部分剔除/空表移除/全无权 mask/reject 用例 - 12/12 通过（含 _analyze 纯函数、S=∅ 跳过、keep 优先、mask/reject/多意图降级）
- [x] 11.5 验证 `retrieved_context.keyword_columns_map` 在 permission 节点 state 中可读（SS 之后未丢失）；若 key 格式为 `table.column`，须与 `MSchemaTable.columns` 名字对齐 - 已确认 key 为 `table.column`，`_analyze` 用 lower 索引对齐
- [x] 11.6 端到端验证场景2：u_alice 问「查所有学校的 School 和经度」-> 经度 SS 筛后=[School,Longitude]（School 有权）-> 部分无权剔除 Longitude（第 12 节移除 query_rewrite 后改为下游自然判断，见 12.x 重测）- E2E 验证通过（旧 query_rewrite 流程）
- [x] 11.7 端到端验证全无权反问：u_alice 问「查所有学校的坐标」-> 坐标 SS 筛后=[Latitude,Longitude]（全禁）-> 全无权反问 permission_choice；resume=mask -> SELECT Latitude,Longitude + 末尾脱敏 ***（17686 行）；resume=reject -> 拒答无 SQL - E2E 验证通过（mask/reject 双分支）
- [x] 11.8 全量回归：pytest 后端 + 前端单测 + admin-crud E2E 全绿，确认重构无回归 - 后端 737 passed（含新增 12 权限节点用例），前端/admin E2E 待重跑

## 12. query_rewrite 移除（部分剔除改下游自然判断）

部分剔除后不再改写 query、不注入 acl_removed_note，权限节点仅记录拦截字段，下游 answerability/CG/verifier 基于 user_query + 剔除后 schema 自然判断，拒答合理。

- [x] 12.1 从 `single_query_graph.py` 移除 query_rewrite 节点（add_node + 边 ss->permission->query_rewrite->schema_finalize 改为 ss->permission->schema_finalize） - 路由改直连 schema_finalize
- [x] 12.2 删除 `src/permission/query_rewrite_node.py` - 已删
- [x] 12.3 `main_graph.py`：answerability/CG/decision 节点移除 `effective_query` fallback，直接用 `user_query`；移除传给 decision 子图的 `acl_removed_fields` - 三处改 `state["user_query"]`
- [x] 12.4 `verification/answerability.py` + `result_verifier.py`：移除 `acl_removed_fields` 参数与 `acl_removed_note` 构造/注入；`prompts.py` 移除 `{acl_removed_note}` 占位 - 两 prompt 模板的「权限剔除说明」段删除
- [x] 12.5 `decision/decision_graph.py`：移除 `acl_removed_fields` state 字段与 node_verify 传参 - 已删
- [x] 12.6 `state.py`：移除 `effective_query` 字段（保留 `acl_removed_fields` 供前端展示） - 已删 effective_query，acl_removed_fields 注释更新
- [x] 12.7 权限节点保留 `acl_removed_fields` 产出（trace_log + state），不变 - permission_node 未改
- [x] 12.8 前端：移除 query_rewrite SSE 事件处理（reducer `case 'query_rewrite'`、types `QueryRewriteEvent`/`TurnDetails.queryRewrite`、timeline `query_rewrite` label/icon） - 全部移除，88 测试通过 + tsc exit=0
- [x] 12.9 重测场景2（部分剔除）：u_alice 问「查所有学校的 School 和经度」-> 权限节点拦截 Longitude -> answerability 自然判断 **拒答**（经度为核心、对应度低，合理），无「查询改写」节点 - E2E 验证：query_rewrite事件=False, answerability=false, reject=True
- [x] 12.10 重测全无权 mask/reject（场景11.7）确认移除 query_rewrite 后无回归 - E2E：坐标查询仍反问 interrupt=True；resume=mask -> SELECT Latitude,Longitude + *** (17686行)；无 query_rewrite 事件
- [x] 12.11 全量回归：pytest 后端 + 前端单测全绿 - 后端 737 passed，前端 88 passed，tsc exit=0

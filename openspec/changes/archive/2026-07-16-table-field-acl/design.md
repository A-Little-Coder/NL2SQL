## Context

NL2SQL 当前以 `user_id` 仅为个性化记忆键（术语偏好/常用表/指标定义），无任何访问控制。整条 `single_query_pipeline`（`ir -> ss -> schema_finalize -> answerability_check -> cg -> execution -> decision`）将召回的全部 schema 暴露给 LLM 生成 SQL，任何用户均可查询敏感表/字段。

关键约束：
- **多库架构**：`DbContextPool` 按 `db_id` 懒加载，权限天然 per-(db_id, user)。
- **应用层执行**：sqlite 直连，数据库自身无 RLS，行级/字段级控制只能在应用层做。
- **已有反问基建**：rewrite 子图已用 `interrupt`/`resume` + `event_cache` pending 暂存 + 前端 tag UI；`ErrorType.PERMISSION_ERROR` 已定义且被 `SQLFixLoop` 标记为 `UNFIXABLE`（伏笔，但当前无逻辑触发）。
- **cache 短路**：`cache_hit` 命中时跳过 `ir/ss/cg` 直奔 execution，是权限绕过风险点。
- **LLM 黑盒**：基于所见 schema 生成 SQL，权限须在 schema 暴露与执行多点协同。

## Goals / Non-Goals

**Goals:**
- 表级 + 字段级权限：敏感表/字段对无权用户脱敏，不直接拒答破坏体验。
- 关键词级判断：部分无权静默剔除，全无权反问确认脱敏，语义自洽。
- 复用现有 interrupt/resume 反问链路，前端零新增交互组件。
- cache 跨用户隔离，脱敏覆盖所有执行路径（含 cache）。
- 独立后台管理 RBAC 黑名单策略。

**Non-Goals:**
- 字段值/行级权限（预留 `field_value_acl.db`，本期不实现）。
- 真登录/token 认证（沿用可信前端传 `user_id`）。
- SQL 层脱敏（`SELECT '***' AS phone`），本期用应用层脱敏。
- 安全护栏（真实值内存暂存、日志脱敏），本期不做。
- 按字段配置差异化脱敏规则（手机号中间4位等），本期统一 `***`。

## Decisions

### D1: 黑名单模型而非白名单
黑名单存"禁止访问的表/字段"，其余默认放行。
- **理由**：内部系统敏感字段少（薪资/手机号/身份证/密码），黑名单维护量小；新增业务表默认可见，DBA 不必每加表跑授权。白名单虽默认更安全，但"每角色每表每字段"配置量在表多的库里压垮运维。
- **风险**：默认放行=新增表忘配黑名单则自动暴露。靠"DBA 加表同步配黑名单"流程兜底。
- **多角色**：员工挂多角色，有效黑名单=各角色 deny_rules 并集（黑名单只禁不放开，并集即最严，语义正确）。

### D2: 权限节点位于 SS 之后、schema_finalize 之前
- **理由**：SS 已将 schema 收窄到候选字段集，此刻裁剪成本最低；之后 `schema_finalize` 算 join_paths、`answerability_check` 判可回答性，均基于裁剪后 schema，自然协同。
- **Alternative**：IR 之后裁剪（召回集合最大，裁剪更早但可能影响 SS 精选质量，且 IR 召回字段数多裁剪开销大）。SS 之后更优。
- **数据可得性**：SS 产出 `selected_schema: List[MSchemaTable]` 已按表聚合、丢失关键词维度。权限节点须**回捞** `retrieved_context` 的 keyword_groups（phrase->columns 映射）做"按关键词判断全无权"。

### D3: 关键词级判断--部分无权剔除、全无权反问脱敏
对每个关键词 K 的召回字段集 S（S = `keyword_columns_map[K]` 与 `selected_schema` 的交集字段）：
- S = ∅（SS 后该关键词无任何字段进入 `selected_schema`）-> **跳过**，不参与全无权判断（SS 已判定该关键词字段与本次查询无关，无需因它反问）。
- S 有可用字段（部分无权）-> 剔除无权字段、保留可用字段往后走，**不脱敏**（final_sql 不含黑名单字段）。
- S 非空且全部无权 -> 该关键词为「全无权关键词」。
- **查询级聚合规则（核心语义，实现曾在此偏离）**：**任一**关键词全无权 -> 对整条 query 触发 `interrupt` 反问"脱敏继续/放弃"。**不是**「所有关键词所有字段都没权限」才反问。即：只要存在一个全无权关键词组即反问，反问时整条 query 暂停。
  - 选脱敏 -> **全无权关键词的字段保留**参与生成（不剔除），末尾脱敏节点脱敏；其余部分无权关键词的字段仍按部分无权剔除。
  - 选放弃 -> 整条 query 拒答（即便其他关键词有权字段也一并放弃）。
- **理由**：全无权=用户对该关键词拿不到任何真值，须告知并让其选；部分无权=有替代真值，静默剔除不打扰。不对称是有意的。
- **剔除后空表**：若某表被裁剪到一列不剩，须从 `selected_schema` 移除，避免空表污染 join_paths 与 CG。
- **不在任何关键词组内的字段**：`selected_schema` 中未被 `keyword_columns_map` 任何 phrase 召回的字段（如 SS 自选/桥接表字段），不参与全无权判断（无关键词为它「背书」），但仍受脱敏节点 final_sql 自判覆盖。

### D3b: 部分剔除后下游自然判断（不改写 query、不注入 note）
部分无权剔除后，`user_query` 仍含被剔除字段的概念（如"经度"被剔除但 query 仍说"和经度"）。**不新增 query_rewrite 节点改写 query**，也不向 answerability/verifier 注入 `acl_removed_note` 强制放行：
- 权限节点仅记录被拦截字段 `acl_removed_fields`（写 state + trace_log + 前台展示"拦截字段"），满足"权限节点清晰写清楚"。
- 下游 answerability / CG / verifier 直接用 `user_query` + 剔除后 `selected_schema` 自然判断：
  - CG 受 schema 约束（被剔字段已不在 schema），无法选出该字段，按剩余 schema 生成 SQL；
  - answerability / verifier 基于关键词-字段对应度判断，若被剔字段为核心、对应度低 -> 拒答，属**合理结果**（用户要的字段无权，拒答而非误导）。
- **理由**：query_rewrite + acl_removed_note 是早期为规避 answerability 误拒答加的双保险，二者重叠且 acl_removed_note 文案"不要判 false"会强行放行、与"拒答合理"相悖。改为透明记录 + 自然判断，更诚实、少一次 LLM 调用、少一个节点。
- **全无权 mask 路径无影响**：选脱敏时字段保留、`acl_removed_fields` 不设，下游本就自然放行，末尾脱敏节点脱敏。
- **NL2SQLState**：保留 `acl_removed_fields`（权限节点产出，供前端展示）；移除 `effective_query`（不再有 query_rewrite 产出）。

### D4: 脱敏节点不依赖标记传递，基于 final_sql 自判
脱敏节点挂在 execution 之后，**自己解析 `final_sql` 涉及字段过黑名单**，命中列脱敏。不要求权限节点传 `mask_fields`。
- **理由**：部分无权剔除后 final_sql 不含黑名单字段->脱敏节点无命中->不脱敏（自然）；全无权脱敏分支保留字段->命中->脱敏；cache 路径同理。少传一个状态、少一个出错点，cache 路径自动正确。
- **Alternative**：权限节点设 `mask_fields` 标记传给脱敏节点。被否：cache 路径跳过权限节点，标记丢失，需额外存历史 `mask_fields`，复杂。

### D5: 表级黑名单展开为字段级
表级规则（如 `audit_log.*`）展开为"该表所有字段无权限"，与字段级黑名单走同一条脱敏路径。
- **理由**：统一处理逻辑，表级/字段级无分支；表级黑名单天然 ⊇ 字段级效果，无优先级问题。

### D6: 反问复用 interrupt/resume，kind=permission_choice（方案 B：subgraph 改造）
权限节点在 single_query_graph 内 SS 之后 `interrupt({question, kind:"permission_choice", options:[{label:"脱敏继续",value:"mask"},{label:"放弃",value:"reject"}]})`。query.py 现有 `__interrupt__` 分支 emit `clarification` 事件，前端 tag UI 复用。

**方案 B 核心改造**：原 `make_run_single_query_node` 用 `single_query_graph.invoke(state)` 手动调用（main_graph.py:1106），子图内 interrupt 无法 resume（invoke 不参与主图 stream/checkpointer）。改为把 single_query_graph 作为 **subgraph 节点编译进 main_graph**（`graph.add_node("run_single_query", single_query_graph)`），子图与主图共享 NL2SQLState schema，子图 interrupt 被主图 checkpointer（InMemorySaver）接管，一次跑完、resume 时从子图 interrupt 处恢复。废弃 make_run_single_query_node 的手动 invoke+合并（subgraph 自动合并 partial state）。

**多意图路径降级**：SubqueryOrchestrator.run 仍用 invoke（subquery_orchestrator.py:108）串行编排，非 graph 拓扑，不支持子查询级 interrupt。多意图子查询全无权时直接拒答该子查询（不反问）；仅单意图路径支持反问脱敏。本期接受此范围限定。

**影响功能点（逐一测试保护）**：
1. 单意图路径 subgraph 调用 + 状态合并（final_sql/final_result 回主图）
2. 权限节点子图内 interrupt + resume（mask/reject 两分支）
3. 多意图路径回归 + 全无权子查询直接拒答
4. cache_hit 路径回归（跳过权限节点，脱敏覆盖）
5. SSE 事件：子图内节点 stage 事件推送（run_single_query 外层 stage 丢失，可接受）
6. 现有反问（rewrite/cache_confirm）回归不受影响
7. checkpointer 持久化子图中断点（InMemorySaver）

**风险**：subgraph interrupt 被主图 checkpointer 接管是 LangGraph 标准行为，但需实测验证（tasks 第一步：最小 subgraph interrupt 测试）。若验证失败，回退方案 C（两阶段重跑 ir/ss）。

### D7: cache 隔离用向量检索 user_id metadata 过滤
`history_cache` 走 `HybridSessionRetriever`（chroma dense + bm25），非 KV 查找。隔离方式=检索时 `where={"user_id": current_user_id}` 过滤，召回阶段只捞当前用户历史。
- **理由**：比"缓存 key 带 user_id"准确（向量召回本无精确 key）；A 的 SQL 不进 B 候选池。
- **脱敏覆盖 cache**：cache 路径跳过权限节点不反问，但脱敏节点对 cache 路径同样生效（D4 自判）。语义自洽：cache 命中说明用户之前已同意脱敏，再命中直接脱敏返回。
- **待核实**：`session_retriever` 入库 turn 是否带 user_id metadata、检索是否支持 where filter。落 tasks 核实 `session_recall.py`/`history_cache.py`。

### D8: 脱敏节点用 sqlglot 解析 SQL
脱敏（含 C 聚合脱敏）需识别 final_sql 涉及字段、聚合函数内字段、列别名。用 `sqlglot` 解析 AST。
- **聚合脱敏（C）**：`AVG(salary)` 若 salary 无权限，则该结果列脱敏。聚合本身字段无权限即脱敏。
- **别名**：`salary AS s` 须认出 s 对应 salary，对结果列 s 脱敏。
- **新增依赖**：`sqlglot`。

### D9: 简单脱敏，统一 ***
无权限列统一替换 `***`，deny_rules 不加 mask 规则字段。
- **理由**：本期求简，差异化脱敏（手机号中间4位等）留后续。

### D10: auth 独立元数据库 + RBAC 表结构
`auth/table_field_acl.db`，表结构：
```
roles(role_id, name)
users(user_id, name, dept, ...)          -- 员工,带属性供将来行级用
user_roles(user_id, role_id)             -- 多角色
deny_rules(id, db_id, role_id, table_pattern, column_pattern, reason)
```
- deny_rules 统一表达表级/字段级：`column_pattern` 缺省=整表禁；通配 `*` 匹配任意。
- 带 `db_id` 适配多库；策略 per-(db_id, role)。
- 匹配：字段(T,C) 被禁 ⟺ 存规则 `table_pattern` 匹配 T 且（`column_pattern` 缺省或匹配 C）。
- **预留**：`auth/field_value_acl.db` 供将来字段值/行级权限。

### D11: 后台 /admin 共用 frontend 工程
独立 `/admin` 路由，复用 `frontend/`（React+Vite+AntD+Zustand），不新建工程。新增员工/角色/黑名单管理页面 + REST API client。

### D12: 应用层脱敏而非 SQL 层脱敏
LLM 生成含黑名单字段的 SQL，执行后在应用层对结果列掩码。
- **理由**：SQL 层脱敏需重写 LLM 的 SQL（`SELECT '***' AS phone`），复杂度高且破坏 CG/SmartFix 的 SQL 语义。应用层脱敏简单。
- **风险**：真实值在执行后、脱敏前短暂存在于应用内存。本期接受（Non-Goal 安全护栏）。

## Risks / Trade-offs

- [新增表忘配黑名单->自动暴露] -> 靠 DBA 加表同步配黑名单流程；后台提供"未配置黑名单的表清单"提醒视图（可选）。
- [sqlglot 解析复杂 SQL（子查询/UNION/嵌套聚合）遗漏字段->漏脱敏] -> 脱敏节点解析失败时**保守脱敏**（命中已知黑名单字段即脱敏）+ 单测覆盖典型 SQL 形态；解析不了的列若疑似黑名单字段则脱敏。
- [权限节点回捞 retrieved_context keyword_groups 字段名不符] -> tasks 阶段先核实 `retrieved_context` 真实结构再编码。
- [single_query_graph 未挂 checkpointer 导致 interrupt 失效] -> tasks 阶段核实并补挂；若无法 interrupt，降级为"全无权直接拒答"（丢失脱敏选项）作为兜底。
- [应用层脱敏真实值内存暂存/日志泄露] -> 本期 Non-Goal，记为已知限制；后续可加日志脱敏护栏。
- [黑名单通配匹配性能] -> deny_rules 规则量小（敏感字段少），内存加载+预编译，无性能问题。
- [实现偏离设计：全无权判断误用整 schema 空判断] -> 初版 permission_node 用「selected_schema 剔除后全空」判全无权，等价于「所有关键词所有字段都没权限」才反问，与 D3「任一关键词全无权即反问」相悖（如「姓名+薪资」会静默剔除薪资而非反问）。修正：改为按 `keyword_columns_map` 分组，逐关键词判全无权，任一命中即反问。见 tasks 第 11 节。

## Migration Plan

1. 新增 `auth/table_field_acl.db` 与 `src/permission/` 模块，对现有流水线零侵入（权限节点默认全放行、脱敏节点默认无命中）。
2. 在 `single_query_graph` 插入权限节点/脱敏节点，feature flag 控制（环境变量 `TABLE_FIELD_ACL_ENABLED`，默认关），灰度开启。
3. 后台 `/admin` 上线，管理员配置角色与黑名单。
4. 开启 flag，按角色灰度验证。
5. **回滚**：关 flag，权限节点直通、脱敏节点直通，流水线恢复原状；auth.db 独立可删，不影响业务库。

## Open Questions（已核实 2026-07-15）

- `retrieved_context` 结构：**已确认** `keyword_columns_map: Dict[str, List[str]]` 提供 phrase->["table.column"] 映射（information_retrieval.py:97），权限节点可直接回捞，D2 成立。
- `single_query_graph` checkpointer：**部分确认** single_query_graph 自身 compile 未挂 checkpointer（single_query_graph.py:128），但作为 main_graph 子图被编排、main_graph 挂了 checkpointer（main_graph.py:1263）。权限节点 interrupt 预期被父图 checkpointer 接管；实现 task 4.3/4.4 时验证，若不工作则降级（节点上移 main_graph 或全无权直接拒答）。
- `session_retriever` user_id 隔离：**已实现** session_recall.py 已完整支持——ChromaSessionQueryIndex.query_dense where filter 含 user_id（line 186-193）、BM25/JSON store 均 按 user_id 过滤、入库 to_metadata 带 user_id。D7 跨用户隔离已是现状，task 6.1/6.2 实质完成，仅剩确认 history_cache 复用走该路径 + 单测。
- deny_rules 通配：**已定** 简单 `*` 通配（fnmatch 风格），column_pattern 缺省=整表禁。

## Purpose

Define the single query pipeline graph that orchestrates the IR, SS, permission, schema finalize, answerability, CG, execution, and decision stages for a single user intent.
## Requirements
### Requirement: SS 未选出表时显式拒答

`schema_finalize`（或 SS）节点检测到 `selected_schema` 为空时 SHALL 设置 `rejection_reason`（友好提示，如"未在数据库中找到与查询相关的表或字段，请尝试换一种表述或确认数据范围"）并 emit `schema_empty` SSE 事件（携带 `reason`），随后路由到 END。MUST NOT 静默 END--即不得在未设置 `rejection_reason` 且未 emit `schema_empty` 的情况下直接结束流水线。

#### Scenario: 未选出表时显式拒答
- **WHEN** SS / schema_finalize 产出 `selected_schema=[]`
- **THEN** state 设置 `rejection_reason` 为友好提示
- **AND** emit `schema_empty` 事件（reason 同 rejection_reason）
- **AND** 流水线路由到 END，不进入 answerability_check / cg

#### Scenario: 选出表时正常放行
- **WHEN** `selected_schema` 非空
- **THEN** 不 emit `schema_empty`，不设拒答 reason，正常进入 answerability_check / cg

### Requirement: Single Query Pipeline Graph

系统 SHALL 提供一个编译好的 `single_query_graph`，以 `NL2SQLState` 为输入与输出 schema，依次执行 ir → ss → schema_finalize → permission → answerability_check（若启用）→ cg → execution → decision 八个阶段，作为单意图路径、history_cache 命中路径与多意图串行编排三处共用的单一事实来源。`schema_finalize` 节点 SHALL 位于 `ss` 之后、`permission` 之前，负责基于收窄后的 `selected_schema` 计算表间 JOIN 路径、补充桥接表 M-Schema、产出 `join_paths_text`。`permission` 节点 SHALL 位于 `schema_finalize` 之后、`answerability_check`（或未启用时的 `cg`）之前，负责按用户角色权限过滤字段。

#### Scenario: 单意图路径走子图
- **WHEN** 用户提交单意图查询
- **THEN** 主图 invoke `single_query_graph` 子图
- **AND** 子图内依次执行 ir → ss → schema_finalize → permission → answerability_check → cg → execution → decision

#### Scenario: cache 命中路径跳过子图中间阶段
- **WHEN** `cache_hit=True` 且 `cached_sql` 非空
- **THEN** `single_query_graph` 直接从入口条件边走向 execution 节点
- **AND** 不执行 ir/ss/schema_finalize/permission/answerability_check/cg

### Requirement: Main Graph Slimming

主图 SHALL 移除 ir / ss / answerability_check / cg / execution / decision 六个独立节点及其间的条件边，替换为单个 `run_single_query` 节点（invoke `single_query_graph`）；主图 SHALL 移除 `route_after_cache` 分支（短路逻辑下沉到子图入口）。主图入口从 `START → history_cache` 改为 `START → rewrite_node`，`rewrite_node` 之后接 `history_cache`。

#### Scenario: 主图 entry 路径变更
- **WHEN** 查询请求进入主图
- **THEN** 首先进入 `rewrite_node`
- **AND** 之后进入 `history_cache`
- **AND** 最后进入 `run_single_query` 子图

#### Scenario: 主图不再直接编排子阶段
- **WHEN** 主图执行 `run_single_query` 节点
- **THEN** 主图不直接编排 ir/ss/cg/execution/decision 等阶段
- **AND** 这些阶段由 `single_query_graph` 子图自行编排

### Requirement: History Cache Hit Short-Circuit

`single_query_graph` SHALL 在入口条件边识别 `cache_hit == True` 且 `cached_sql`（或 `adjusted_cached_sql`）非空，直奔 `execution` 节点（从 `adjusted_cached_sql` 构造候选，缺失时回退 `cached_sql`），随后正常进入 `decision`。`value_rewrite` 与 `cache_confirm` 阶段位于主图（`history_cache` 之后、`run_single_query` 之前），见 `history-cache-reuse` 能力；`single_query_graph` 不再承载命中确认逻辑。`cache_hit` 为 True 但 `cached_sql` 与 `adjusted_cached_sql` 均为空时 SHALL 直接 END 并在 state 中写 `error`。`history_cache` 节点从主图入口后移一位，改为接收重写后的 `user_query`。改写后的完整语义 query 进入 cache 命中检测，提高匹配率。

#### Scenario: rewrite 拒答不进入 cache
- **WHEN** Rewrite 节点拒答（`rejection_reason` 非空）
- **THEN** 主图 SHALL 直接 END，不进入 `history_cache`

#### Scenario: rewrite 放行后 cache 命中
- **WHEN** Rewrite 节点改写 `user_query` 后放行，且改写后的 query 匹配历史缓存
- **THEN** `history_cache` SHALL 基于改写后的 `user_query` 做命中检测，命中后走 `value_rewrite → cache_confirm → run_single_query` 路径

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


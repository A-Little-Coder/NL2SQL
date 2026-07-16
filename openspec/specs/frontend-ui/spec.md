## Purpose

Define the frontend UI interactions and display requirements for the NL2SQL application, including detail inspector, timeline, and admin panels.
## Requirements
### Requirement: IR 节点按关键词组聚合展示字段与值召回

IR 详情检查器 SHALL 按关键词组逐组展示：每组含 phrase、同义词 terms、召回字段（带 score）、召回值（带 score）。值召回的组归属 MUST 直接使用后端 `schema_recall` 事件中 `keyword_groups[].values`（已由后端 `source_phrase` 归属），前端 MUST NOT 自行按 `table.column` 猜测归属。

#### Scenario: 多关键词组逐组渲染
- **WHEN** `schema_recall` 事件携带多个 keyword_groups
- **THEN** IR 详情按组渲染，每组一个区块，展示 phrase 标题、terms 同义词标签、召回字段列表（含 table.column 与 score）、召回值列表（含 value 与所属 table.column 与 score）

#### Scenario: 空召回组展示占位
- **WHEN** 某关键词组的 columns 与 values 均为空
- **THEN** 该组仍渲染，字段与值区域展示"无召回"占位
- **AND** 不隐藏该组

#### Scenario: 值召回准确归属不猜测
- **WHEN** 某 value 的所属列被多个关键词组召回
- **THEN** 该值仅出现在后端 `source_phrase` 指定的组下
- **AND** 前端不按 `table.column` 反查自行归属，避免多组误归

### Requirement: SS 阶段在时间轴可见

时间轴 SHALL 为 `ss`（Schema Selection）阶段渲染独立节点。`ss` 与 `schema_finalize` 的 `stage` 事件 MUST 点亮该节点，`schema_finalize` 事件 MUST 更新节点摘要（如展示 join_edges/bridge_tables）。

#### Scenario: SS 节点点亮
- **WHEN** SSE 流推送 `stage(ss, started)` 与 `stage(ss, done)`
- **THEN** 时间轴出现"Schema 选择"节点并按 started/done 切换状态
- **AND** 节点摘要展示选出的表/列数（若 stage 携带）

#### Scenario: schema_finalize 更新 SS 节点
- **WHEN** `schema_finalize` 事件到达（含 join_edges/bridge_tables）
- **THEN** SS 节点摘要更新为含 join_edges 与 bridge_tables 信息
- **AND** 检查器 SS 详情区展示 join_edges 与 bridge_tables

### Requirement: 三栏宽度可拖拽调整

三栏布局 SHALL 支持用户拖拽左右分隔条动态调整边栏宽度。左右栏 MUST 有最小宽度（折叠阈值）与最大宽度约束，中栏占剩余空间。边栏宽度 MUST 持久化到 localStorage，刷新后恢复。

#### Scenario: 拖拽调整边栏宽度
- **WHEN** 用户拖动左/右分隔条
- **THEN** 对应边栏宽度实时变化，中栏自适应占剩余空间
- **AND** 边栏宽度不超过最大约束、不低于折叠阈值

#### Scenario: 宽度持久化
- **WHEN** 用户调整边栏宽度后刷新页面
- **THEN** 边栏恢复至上次调整的宽度

### Requirement: 边栏窄于阈值时隐藏内容

当边栏宽度被拖拽至折叠阈值以下时，系统 MUST 折叠边栏：仅保留一条窄展开条（含展开按钮），边栏内容组件 MUST 完全 unmount 不渲染。系统 MUST NOT 在中间宽度态压扁边栏内容。

#### Scenario: 拖至阈值以下折叠
- **WHEN** 用户将左/右边栏拖至最小阈值以下
- **THEN** 边栏折叠为窄展开条（►/◄），原内容组件 unmount
- **AND** 不出现内容被压缩变形的中间态

#### Scenario: 点击展开条恢复
- **WHEN** 边栏处于折叠态，用户点击展开条
- **THEN** 边栏恢复至上次展开宽度，内容组件重新 mount

### Requirement: 前置拒答节点在时间轴可见

时间轴 SHALL 为 `pre_reject`（前置拒答检测）阶段渲染独立节点。`stage(pre_reject, started)` SHALL 将该节点置 active 态，`stage(pre_reject, done)` SHALL 将其置 done 态。通过时节点摘要 SHALL 显示"通过"；拒答时（`stage` done 携带 `rejection_reason`）节点 SHALL 置 error 态并展示拒答原因，且前端 MUST NOT 因此再生成通用 `error` 时间轴节点。

#### Scenario: 通过时点亮绿色节点
- **WHEN** SSE 流推送 `stage(pre_reject, started)` 随后 `stage(pre_reject, done)` 且无 `rejection_reason`
- **THEN** 时间轴出现"前置检查"节点，状态由 active 转 done（绿色）
- **AND** 节点摘要显示"通过"

#### Scenario: 写操作拒答置红色节点
- **WHEN** `stage(pre_reject, done)` 携带 `rejection_reason`（如"本服务仅支持查询…"）
- **THEN** "前置检查"节点置 error 态（红色），摘要展示拒答原因
- **AND** 时间轴 MUST NOT 出现通用 `error` 节点（前置拒答身份独占呈现）

#### Scenario: 拒答后无下游节点
- **WHEN** 前置拒答触发
- **THEN** 时间轴不出现信息检索 / SQL 生成等下游节点
- **AND** Turn 状态置 `error` 且 `rejection=true`

### Requirement: 改写检测节点可见并支持多轮

后端 `detect_issues` 子节点 SHALL 在每次检测完成时 emit `rewrite_detect` SSE 事件，携带 `round`（第几次检测，= `rewrite_round + 1`）、`has_issues`、`issue_detail`、`issue_types`。前端 SHALL 为每轮检测渲染独立时间轴节点（id=`detect_r{round}`），摘要表达"无问题"或"检测到 {issue_types}"。Inspector 详情 SHALL 按轮次列表展示全部检测轮次。

#### Scenario: 首轮检测无问题
- **WHEN** `rewrite_detect` 事件到达，`round=1` 且 `has_issues=false`
- **THEN** 时间轴出现"检测 r1"节点（done 态），摘要显示"无问题"
- **AND** Inspector 检测详情记录第 1 轮（has_issues=false）

#### Scenario: 检测到指代缺失
- **WHEN** `rewrite_detect` 事件 `round=1`、`has_issues=true`、`issue_types=["指代缺失"]`
- **THEN** "检测 r1"节点摘要显示"检测到 指代缺失"
- **AND** Inspector 记录该轮 issue_detail 与 issue_types

#### Scenario: 多轮检测各自独立节点
- **WHEN** 流水线先后推送 `rewrite_detect(round=1)` 与 `rewrite_detect(round=2)`
- **THEN** 时间轴出现"检测 r1"与"检测 r2"两个独立节点
- **AND** Inspector 检测详情按轮次顺序列出两轮

#### Scenario: LLM 降级仍 emit
- **WHEN** `detect_issues` 因 LLM 不可用降级为无问题
- **THEN** 后端仍 SHALL emit `rewrite_detect` 事件（`has_issues=false`）
- **AND** 时间轴仍有对应检测节点，不缺位

### Requirement: 改写执行节点可见并支持多轮

前端 SHALL 消费 `rewrite` SSE 事件（`rewritten_query` / `rewrite_reason` / `rewrite_round`），为每轮改写渲染独立时间轴节点（id=`rewrite_r{round}`），摘要表达"改写第 N 轮"。Inspector 详情 SHALL 按轮次列表展示每轮的原句、改写后 query、改写原因。

#### Scenario: 单轮改写
- **WHEN** `rewrite` 事件到达，`rewrite_round=1`、`rewritten_query` 非空
- **THEN** 时间轴出现"改写 r1"节点（done 态）
- **AND** Inspector 改写详情记录第 1 轮（原句、改写后、reason）

#### Scenario: 多轮改写迭代可见
- **WHEN** 先后推送 `rewrite(round=1)` 与 `rewrite(round=2)`
- **THEN** 时间轴出现"改写 r1"与"改写 r2"两个独立节点
- **AND** Inspector 按轮次顺序列出两轮改写

#### Scenario: 改写节点与检测节点交替
- **WHEN** 流水线按 detect r1 -> rewrite r1 -> detect r2 顺序推送事件
- **THEN** 时间轴节点顺序为"检测 r1"、"改写 r1"、"检测 r2"
- **AND** 交替呈现改写迭代过程

### Requirement: 值参数改写节点可见

前端 SHALL 消费 `value_rewrite` SSE 事件，渲染 `value_rewrite` 时间轴节点，摘要表达"已改写值参数"或"未变更"。Inspector 详情 SHALL 展示 historical_query、cached_sql、adjusted_cached_sql、changed、reason。

#### Scenario: 值参数已改写
- **WHEN** `value_rewrite` 事件 `changed=true`
- **THEN** 时间轴出现"值改写"节点，摘要显示"已改写值参数"
- **AND** Inspector 展示 cached_sql 与 adjusted_cached_sql 对比及 reason

#### Scenario: 值参数未变更
- **WHEN** `value_rewrite` 事件 `changed=false`
- **THEN** "值改写"节点摘要显示"未变更"

### Requirement: 复用确认节点可见

前端 SHALL 消费 `cache_confirm` SSE 事件，渲染 `cache_confirm` 时间轴节点。`approved=true` 时摘要显示"确认复用 ✓"，`approved=false` 时显示"重新生成 ✗"。Inspector 详情 SHALL 展示 approved、user_choice、historical_query、user_query。该节点 SHALL 与 `cache`（命中检测）节点独立呈现，MUST NOT 合并。

#### Scenario: 用户确认复用
- **WHEN** `cache_confirm` 事件 `approved=true`
- **THEN** 时间轴出现"确认复用"节点，摘要"确认复用 ✓"
- **AND** Inspector 展示 user_choice 与 historical_query

#### Scenario: 用户选择重新生成
- **WHEN** `cache_confirm` 事件 `approved=false`
- **THEN** "确认复用"节点摘要"重新生成 ✗"

#### Scenario: 确认节点与命中检测节点分离
- **WHEN** cache 命中后用户确认
- **THEN** 时间轴同时存在"缓存命中"节点与"确认复用"节点
- **AND** 两个节点独立呈现，不合并

### Requirement: 多轮节点按 id 独立呈现

`TimelineNode` SHALL 支持可选 `id` 字段。reducer 的 `upsert` SHALL 在节点携带 `id` 时按 `id` 匹配合并、无 `id` 时回退按 `type` 匹配。单次节点（cache / ir / ss / answerability / cg / execution / decision / result / error / clarify / pre_reject / value_rewrite / cache_confirm）MUST NOT 携带 `id`，保持按 type 合并行为；多轮节点（`rewrite_detect` / `rewrite`）MUST 携带 `id`（`detect_r{round}` / `rewrite_r{round}`）以独立呈现。

#### Scenario: 多轮节点按 id 区分
- **WHEN** reducer 处理两个 `rewrite` 事件，id 分别为 `rewrite_r1`、`rewrite_r2`
- **THEN** 时间轴保留两个独立节点，不合并

#### Scenario: 单次节点无 id 回退 type 合并
- **WHEN** 多次 `stage` / 业务事件更新同一单次节点（如 ir）
- **THEN** 该节点按 type 合并，不产生重复节点
- **AND** id 改造对单次节点零回归

#### Scenario: 节点点击 pin 到 type
- **WHEN** 用户点击某轮"改写 r2"节点
- **THEN** `selectedNode` 置为 `rewrite`（type 级别）
- **AND** Inspector 展示改写全部轮次列表

### Requirement: 前置拒答 LLM 判定类别可见

`pre_reject` 节点 SHALL 展示 LLM 判定类别（`写操作` / `危险信息` / `通过`）。`stage(pre_reject, done)` 携带 `category` 时，前端 SHALL 将其写入 `details.preReject.category`，Inspector 详情 SHALL 展示类别标签与拒答原因。拒答时节点置 error 态并展示 category + reason；通过时类别为"通过"。前置拒答触发时时间轴 MUST NOT 出现通用 `error` 节点。

#### Scenario: 危险信息指令拒答
- **WHEN** `stage(pre_reject, done)` 携带 `category="dangerous_info"` 与 `rejection_reason`
- **THEN** "前置检查"节点置 error 态，Inspector 展示类别"危险信息"与原因
- **AND** 时间轴 MUST NOT 出现通用 `error` 节点

#### Scenario: 写操作拒答展示类别
- **WHEN** `stage(pre_reject, done)` 携带 `category="write_op"` 与 `rejection_reason`
- **THEN** "前置检查"节点置 error 态，Inspector 展示类别"写操作"

#### Scenario: 通过时类别为通过
- **WHEN** `stage(pre_reject, done)` 无 `rejection_reason` 且 `category="normal"`
- **THEN** "前置检查"节点 done 态，摘要"通过"，Inspector 类别"通过"

### Requirement: schema 选择全空拒答节点可见

前端 SHALL 消费 `schema_empty` SSE 事件（`reason`），渲染 `schema_empty` 时间轴节点（error 态），摘要展示"未匹配相关表"或拒答原因。Inspector 详情 SHALL 展示 reason。该节点出现时时间轴 MUST NOT 出现下游节点（SQL 生成 / 执行等）。

#### Scenario: schema 全空展示拒答节点
- **WHEN** `schema_empty` 事件到达（`reason="未在数据库中找到与查询相关的表或字段…"`）
- **THEN** 时间轴出现"未匹配表"节点（error 态），摘要展示原因
- **AND** Inspector 展示 reason
- **AND** 时间轴不出现 SQL 生成等下游节点

### Requirement: 详情检查器跨轮锁定与自动跟随

store SHALL 新增顶层 `inspectorTurnId: string | null`（`null` = 自动跟随最新 turn）。`DetailInspector` SHALL 读取 `inspectorTurnId` 对应的 turn；`inspectorTurnId === null` 时 SHALL 回退读取最后一个 turn（自动跟随最新）。`selectNode(turnId, node)` SHALL 在设置该 turn 的 `selectedNode` 同时将 `inspectorTurnId` 置为 `turnId`（锁定到该 turn）。点击当前已选中节点 SHALL 将 `inspectorTurnId` 与该 turn 的 `selectedNode` 同置为 `null`（恢复全自动跟随）。`inspectorTurnId` 锁定到非最新 turn 时，新 turn 开始 SHALL NOT 自动切换检查器；检查器 SHALL 显示"已锁定到第 N 轮"并提供"返回最新"按钮，点击 SHALL 调用 `releaseInspector()` 置 `inspectorTurnId=null`。

#### Scenario: 默认自动跟随最新 turn
- **WHEN** `inspectorTurnId === null` 且存在多个 turn
- **THEN** 检查器显示最后一个 turn 的节点详情（自动跟随最新节点或其 `selectedNode`）
- **AND** 行为与本次改动前一致

#### Scenario: 点击旧轮节点锁定到该轮
- **WHEN** 用户点击第 1 轮（非最新）的 IR 节点
- **THEN** `inspectorTurnId` 置为第 1 轮的 turnId，第 1 轮 `selectedNode='ir'`
- **AND** 检查器切换显示第 1 轮的 IR 详情

#### Scenario: 锁定后新轮不自动切换检查器
- **WHEN** 检查器锁定在第 1 轮，用户发起新查询产生第 2 轮
- **THEN** 检查器保持显示第 1 轮详情，不跟随第 2 轮
- **AND** 检查器顶部显示"已锁定到第 1 轮"与"返回最新"按钮

#### Scenario: 返回最新解除锁定
- **WHEN** 检查器锁定在旧轮，用户点击"返回最新"按钮
- **THEN** `inspectorTurnId` 置为 `null`
- **AND** 检查器切回显示最新 turn

#### Scenario: 点击已选中节点解除锁定
- **WHEN** 用户点击当前已锁定的节点（`selectedNode` 已是该 type）
- **THEN** `inspectorTurnId=null` 且该 turn `selectedNode=null`
- **AND** 检查器恢复全自动跟随最新 turn 的最新节点

### Requirement: 权限管理后台页面
系统 SHALL 在 frontend 工程提供独立 /admin 路由的权限管理后台，支持员工/角色 CRUD 与表/字段黑名单配置（通配模式），配置后对前台问数即时生效。

#### Scenario: 后台配置黑名单即时生效
- **WHEN** 管理员在 /admin 新增角色黑名单规则并绑定员工
- **THEN** 规则持久化到 auth/table_field_acl.db，该员工前台查询即时受规则约束


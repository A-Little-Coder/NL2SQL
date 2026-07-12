## ADDED Requirements

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

## ADDED Requirements

### Requirement: SSE 推理流实时渲染到时间轴

系统 SHALL 订阅 `POST /api/v1/query` 的 SSE 流，将每个事件实时渲染为时间轴上的节点，节点按到达顺序点亮，使 Agent 推理过程对用户可见。系统 MUST 处理 `stage`、`cache_check`、`keywords`、`schema_recall`、`answerability`、`sql_candidates`、`execution`、`final_decision`、`result`、`error`、`done` 全部事件类型。

#### Scenario: 逐事件点亮时间轴节点
- **WHEN** SSE 流依次推送 `stage(ir, started)`、`keywords`、`schema_recall`、`stage(ir, done)`、`stage(cg, started)`、`sql_candidates` 等事件
- **THEN** 时间轴按到达顺序逐个点亮 ir / cg 等节点，每个节点显示一行摘要（如"3 候选"），未到达节点保持未点亮
- **AND** 首字节到达后 5 秒内时间轴出现可见变化（不为整条 query 完成才一次性渲染）

#### Scenario: 历史缓存命中短路
- **WHEN** 首个事件为 `cache_check` 且 `hit=true`
- **THEN** 时间轴仅点亮"缓存命中"节点并标注 `confidence` 与 `source`
- **AND** 直接展示缓存的 `final_sql` 与结果，跳过 ir/ss/cg/execution 节点的渲染

#### Scenario: 拒答时显示拒绝理由
- **WHEN** `error` 事件 payload 含 `rejection: true`
- **THEN** 在时间轴末端展示拒绝理由，不渲染结果表格，`Turn.status='error'`
- **AND** 后续 `done` 事件到达后标记该轮结束

#### Scenario: 通用错误事件
- **WHEN** `error` 事件到达（非 rejection）
- **THEN** 展示错误信息（含 `node` 若有），`Turn.status='error'`，不再期待 `result`

#### Scenario: done 事件收尾
- **WHEN** `done` 事件到达
- **THEN** 根据 `has_result` 决定是否展示结果区，根据 `awaiting_clarification` 决定是否进入反问等待态，根据 `fix_failed`/`decision_path` 展示决策路径与修复轮次信息

### Requirement: 节点详情检查器

系统 SHALL 在右侧分栏提供节点详情检查器，展示用户选中（或自动跟随）的时间轴节点的完整结构化产物（候选 SQL 全文、执行结果、思考链、决策理由等）。

#### Scenario: 自动跟随最新节点
- **WHEN** 新节点完成且 `selectedNode` 为 null
- **THEN** 检查器自动显示该最新节点的详情

#### Scenario: 点击节点锁定
- **WHEN** 用户点击时间轴上某个已完成节点
- **THEN** 检查器锁定显示该节点详情，后续新节点完成不再切换检查器内容，直至用户解除锁定

#### Scenario: 新查询重置为自动跟随
- **WHEN** 用户发起新一轮查询
- **THEN** `selectedNode` 重置为 null，检查器恢复自动跟随最新节点

### Requirement: qwen3 思考链打字机与心跳保活

系统 SHALL 将 `llm_thinking` 事件按节点累积并以打字机式实时滚动展示；系统 MUST 正确处理 `: heartbeat` 注释行以重置客户端读超时，且不将其渲染为可见事件。

#### Scenario: qwen3 思考链实时累积
- **WHEN** 同一节点连续推送多个 `llm_thinking` 事件（`data.node` 相同，`data.text` 为片段）
- **THEN** 检查器中该节点的思考链区域按到达顺序拼接 `text` 并实时滚动显示
- **AND** 思考链区域默认折叠，可展开

#### Scenario: 心跳注释行不产生可见事件
- **WHEN** SSE 流推送 `: heartbeat` 注释行
- **THEN** 不向时间轴/检查器渲染任何事件
- **AND** 客户端读超时计时器被重置，连接保持

#### Scenario: 非思考模型自动降级
- **WHEN** 整条查询未收到任何 `llm_thinking` 事件（非 qwen3 或未开启思考）
- **THEN** 时间轴与检查器正常推进，不报错、不显示空思考链区域

### Requirement: 反问内联气泡与 resume 续流

系统 SHALL 在收到 `clarification` 事件时以内联气泡形式展示反问问题与选项，收集用户回答后发送带 `resume` 字段的新 `POST /query` 请求恢复执行，并将 resume 流的事件合并到同一对话轮次。系统 MUST 使用客户端生成的 `turnId` 作为轮次主键，而非服务端每次生成的 `query_id`。

#### Scenario: 反问中断展示内联气泡
- **WHEN** `clarification` 事件到达（含 `question`、`ambiguities`、`round`、`awaiting_answer`）
- **THEN** `Turn.status='awaiting_clarification'`，时间轴停在"反问"节点
- **AND** 该节点下方内联气泡展示问题与可选项，不使用模态弹窗

#### Scenario: resume 续流合并到同一轮次
- **WHEN** 用户在气泡中作答并触发 resume 请求
- **THEN** 系统发送 `POST /query`，body 含 `resume=<回答>` 与原 `session_id`/`db_id`/`user_id`
- **AND** 新 SSE 流的事件并入同一 `turnId` 的 `Turn`，时间轴在反问节点之后继续追加节点
- **AND** resume 流产生新的 server `query_id`，但不改变前端 `turnId`

#### Scenario: 多轮反问
- **WHEN** resume 后再次收到 `clarification` 事件且 `round` 递增
- **THEN** 气泡支持再次作答，可多次 resume 直至完成

#### Scenario: 反问期间不展示最终结果
- **WHEN** `Turn.status='awaiting_clarification'`
- **THEN** 不渲染结果表格，直至 resume 流推送 `result` 事件

### Requirement: 多数据库下拉切换

系统 SHALL 通过下拉选择器列出所有可用数据库，用户切换后后续查询使用新选中的 `db_id`。系统 MUST 在首次访问冷库时给出加载提示。

#### Scenario: 列出可用数据库
- **WHEN** 应用加载或用户打开数据库选择器
- **THEN** 调用 `GET /api/v1/databases` 填充下拉项，每项含 `db_id` 与 `db_path`

#### Scenario: 切换数据库
- **WHEN** 用户选择另一个 `db_id`
- **THEN** 后续 `POST /query` 请求体携带新 `db_id`
- **AND** 当前会话的后续查询使用新 `db_id`（会话与 db 的绑定关系由前端管理）

#### Scenario: 冷库首次加载提示
- **WHEN** 首次向某 `db_id` 发起查询（后端懒加载，约 5-10 秒）
- **THEN** 展示明确的 loading 态与文案提示"首次加载该数据库，约需数秒"
- **AND** 时间轴已点亮的节点保持可见，不被 loading 态清除

#### Scenario: 不存在的数据库
- **WHEN** 查询返回 404（`db_id` 不存在）
- **THEN** 友好提示该数据库不存在，不崩溃，下拉选择保持上一个有效值

### Requirement: 会话管理侧栏

系统 SHALL 在左侧栏提供会话管理：列出、新建、查看历史、删除会话，对接 `POST/GET/DELETE /api/v1/sessions` 与 `GET /api/v1/sessions/{id}/history`。

#### Scenario: 列出用户会话
- **WHEN** 侧栏加载
- **THEN** 调用 `GET /api/v1/sessions/{user_id}` 展示会话列表，每项含 `session_id`、`created_at`、`updated_at`、`status`、`turn_count`，按 `updated_at` 降序

#### Scenario: 新建会话
- **WHEN** 用户点击"新会话"
- **THEN** 调用 `POST /api/v1/sessions`（body 含 `user_id`）创建会话并切换至新会话

#### Scenario: 查看会话历史
- **WHEN** 用户点击侧栏某会话
- **THEN** 调用 `GET /api/v1/sessions/{id}/history` 加载历史轮次到对话区，每轮含 `user_query`、`final_sql`、`final_result_sample`、`timestamp`

#### Scenario: 删除会话
- **WHEN** 用户删除某会话
- **THEN** 调用 `DELETE /api/v1/sessions/{id}`，从侧栏移除；若删除的是当前会话则切换至首个会话或空态

#### Scenario: 未知 session_id 自动创建
- **WHEN** 查询请求携带系统中不存在的 `session_id`
- **THEN** 后端自动创建会话，前端在查询完成后刷新侧栏列表以纳入新会话

### Requirement: 结果表格展示

系统 SHALL 将 `result` 事件的行数据以表格形式展示，SQL 以代码块展示并支持复制。系统 MUST 止步于表格，不做图表、不支持 SQL 手改重跑。

#### Scenario: 渲染结果表格
- **WHEN** `result` 事件到达且 `result` 为非空行列表
- **THEN** 取首行 keys 作为列，渲染分页表格，SQL 代码块置于表格上方

#### Scenario: SQL 复制
- **WHEN** 用户点击 SQL 代码块的复制按钮
- **THEN** `final_sql` 写入剪贴板，给出复制成功反馈

#### Scenario: 大结果集分页
- **WHEN** 结果行数超过单页阈值
- **THEN** 表格展示分页控件，默认每页 10 行

#### Scenario: 空结果
- **WHEN** `result` 事件到达但行列表为空
- **THEN** 表格区展示"无数据"占位，SQL 代码块仍展示

### Requirement: 用户记忆可视化

系统 SHALL 提供用户记忆视图，展示 `GET /api/v1/users/{user_id}/memory` 与 `GET /api/v1/users/{user_id}/metrics` 的内容，贯彻透明主题。

#### Scenario: 展示用户长期记忆
- **WHEN** 用户打开记忆视图
- **THEN** 调用 `GET /api/v1/users/{user_id}/memory`，分区块展示 `term_preferences`、`frequently_used_tables`、`metric_definitions`、`query_preferences`、`domain_context`、`clarification_history`

#### Scenario: 展示指标定义
- **WHEN** 记忆视图加载
- **THEN** 调用 `GET /api/v1/users/{user_id}/metrics`，展示指标定义列表，每项含 `name`、`description`、`sql_pattern`、`source`、`confidence`

#### Scenario: 用户切换刷新记忆
- **WHEN** `user_id` 改变（如顶部输入框切换用户）
- **THEN** 记忆视图重新请求并展示对应用户的记忆与指标

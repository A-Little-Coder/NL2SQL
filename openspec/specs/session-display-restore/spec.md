## ADDED Requirements

### Requirement: 展示存储层独立分片结构
系统 SHALL 在项目根 `event_cache/` 目录（与 `data/` 平级）维护按用户的展示存储层，按 `{user_id}/shard_xxxx/{session_id}.json` 三级组织。分片按会话 `created_at` 顺序：每个 shard 目录最多容纳 20 个会话，满 20 后开新 shard。每个会话文件存储该会话全部 turn 的 SSE 事件流。展示存储层 SHALL 与 `session_memory`（`data/sessions/`）复用层物理分离。

#### Scenario: 新会话分配到当前 shard
- **WHEN** 用户已有 15 个会话（均在 shard_0001）并创建第 16 个会话
- **THEN** 第 16 个会话登记到 shard_0001（未满 20）
- **AND** `event_cache/{uid}/shard_0001/{new_sid}.json` 文件创建

#### Scenario: shard 满后开新 shard
- **WHEN** shard_0001 已有 20 个会话，用户创建第 21 个会话
- **THEN** 新会话登记到 shard_0002
- **AND** `event_cache/{uid}/shard_0002/{new_sid}.json` 文件创建

### Requirement: SSE 事件流持久化
系统 SHALL 在查询 SSE 生成器内统一捕获所有已发出的事件（包括 graph 节点 emit 的与路由自构造的 `result`/`error`/`done`），并在 turn 完成时将该 turn 的完整事件序列写入对应会话的 `event_cache` 文件。事件流中每个事件 SHALL 保留其原始 `type` 与 `data` payload，且顺序与发出顺序一致。

#### Scenario: turn 完成写入完整事件流
- **WHEN** 一次无反问的查询完成（turn done）
- **THEN** 该 turn 的全部 SSE 事件（如 stage/keywords/schema_recall/sql_candidates/execution/final_decision/result/done）按发出顺序写入 `event_cache/{uid}/shard_xxxx/{sid}.json`
- **AND** 每个事件的 `type` 与 `data` payload 与实时推给前端的完全一致

### Requirement: 结果行数据 20 行采样
系统 SHALL 在将 `result` 事件写入 `event_cache` 时，将其 `data.result` 行数据截断为前 20 行；实时推给前端的 `result` 事件 SHALL 保留完整行数据。前端从历史重放恢复且结果超过 20 行时 SHALL 展示"历史快照·前20行"提示。

#### Scenario: 存储截断而实时完整
- **WHEN** 一次查询返回 100 行结果
- **THEN** 实时 SSE `result` 事件携带 100 行推给前端
- **AND** `event_cache` 中该 result 事件的 `data.result` 仅保留前 20 行

#### Scenario: 历史重放显示快照提示
- **WHEN** 用户刷新页面后切回一个曾有 100 行结果的历史会话
- **THEN** 结果表格显示前 20 行
- **AND** 表格上方显示"历史快照·前20行"提示

### Requirement: resume 跨阶段事件完整性
对于经历反问的 turn（`query` 阶段 `__interrupted__` 挂起 + `resume` 阶段完成），系统 SHALL 将两阶段发出的事件流合并为完整事件序列后落盘，确保重放能恢复 `query` 阶段的中间过程（关键词、schema 召回、SQL 候选等）。

#### Scenario: 反问 turn 事件合并落盘
- **WHEN** 一次查询在反问处挂起，用户回答后 resume 完成
- **THEN** `event_cache` 中该 turn 的事件流包含 query 阶段事件（keywords/schema_recall/sql_candidates 等）与 resume 阶段事件
- **AND** 前端重放该事件流得到的 Turn 与实时累积的一致

### Requirement: 会话列表分页惰性加载
系统 SHALL 提供按 `created_at` **全局排序后滑动窗口分页**的会话列表接口，每页最多 20 个会话，按 `created_at` 倒序。**`page=0` 始终返回 index 中时间最近的 ≤20 个会话，不因新 shard 创建而跳变。**（注意：shard 写入规则不变——每 shard ≤20 会话、满则开新 shard；仅分页读取逻辑变更。）前端左栏 SHALL 初始只加载最新一页，向下滚动到底时惰性加载更早一页。

#### Scenario: 初始加载最新页
- **WHEN** 用户打开应用，左栏渲染会话列表
- **THEN** 仅加载 index 中 `created_at` 最新的 ≤20 个会话摘要
- **AND** 不加载更早的数据

#### Scenario: 下拉加载更早页
- **WHEN** 用户在左栏滚动到底部
- **THEN** 惰性加载再往前的 ≤20 个会话摘要并追加到列表

### Requirement: 会话创建双写索引
`create_session` SHALL 同时在 `session_memory` 建会话与 `event_cache/{uid}/index.json` 登记会话摘要（含 `session_id`/`created_at`/`turn_count`/`updated_at`/`status`/`shard_id`）。`index.json` SHALL 作为会话列表分页的数据源。`turn done` 时 SHALL 更新 `index.json` 中该会话的 `turn_count` 与 `updated_at`。

#### Scenario: 新建会话登记索引
- **WHEN** 用户创建新会话
- **THEN** `session_memory` 创建会话文件（复用层）
- **AND** `event_cache/{uid}/index.json` 新增该会话摘要条目并分配 `shard_id`

#### Scenario: turn 完成更新索引
- **WHEN** 一个 turn 完成并写入 `event_cache`
- **THEN** `index.json` 中该会话的 `turn_count` 递增、`updated_at` 刷新

### Requirement: 会话切换事件流重放恢复
前端 SHALL 在加载历史会话时，将 `event_cache` 返回的事件流逐个喂给现有 `reduceSseEvent` 重放以重建 `Turn`；`reduceSseEvent` 本身 SHALL 不做任何修改。重放重建的 `Turn` SHALL 与该 turn 实时累积的 `Turn` 在全部字段（timeline 节点、details、thinking、result、clarification、doneMeta 等）上一致。

#### Scenario: 切回历史会话完整恢复
- **WHEN** 用户在会话 A 发起查询（实时完整累积 Turn），切到会话 B，再切回 A（未刷新）
- **THEN** 会话 A 的对话、时间轴全部节点、SQL 候选、检查器细节、思考链、结果均恢复显示
- **AND** 恢复内容与离开时一致（结果行数据若超 20 行则降级为前 20 行 + 快照提示）

#### Scenario: 刷新后重放等价于实时累积
- **WHEN** 用户刷新页面后打开一个有完整事件流的历史会话
- **THEN** 前端重放事件流重建 Turn
- **AND** 重建的 Turn 与当初实时累积的结构一致

### Requirement: 同次会话内存缓存
前端 SHALL 维护按会话的 `turnsBySession` 内存缓存。切换离开会话时 SHALL 将当前完整 `turns` 存入缓存；切回时若缓存命中 SHALL 优先使用缓存（含完整行数据），未命中才走 `event_cache` 重放。

#### Scenario: 同次切回用缓存完整行
- **WHEN** 用户在会话 A 查询得 100 行结果，切到会话 B 再切回 A（未刷新页面）
- **THEN** 会话 A 从 `turnsBySession` 内存缓存恢复
- **AND** 结果显示完整 100 行（不走 20 行截断）

#### Scenario: 刷新后缓存失效走重放
- **WHEN** 用户刷新页面后切回会话 A
- **THEN** `turnsBySession` 内存缓存已失效，走 `event_cache` 重放
- **AND** 结果显示前 20 行 + 快照提示

### Requirement: 老会话摘要回落兼容
对于 `event_cache` 中无事件流的存量会话，系统 SHALL 回落至 `session_memory` 摘要进行简化重建，恢复 `user_query`/`final_sql`/`result_meta` 等摘要信息，行为与本次改动前一致。

#### Scenario: 老会话回落摘要重建
- **WHEN** 用户打开改动前已存在、无 `event_cache` 事件流的会话
- **THEN** 系统从 `session_memory` 读取摘要简化重建
- **AND** 显示 user_query、final_sql、结果行数（无完整时间轴节点、无 SQL 候选、无真实行数据）

### Requirement: 复用层零影响
展示数据迁出后，`session_memory` 复用层 SHALL 保持原有行为不变。`history_cache` 的两条读取路径（LLM 判断消费 `conversation_history`、混合召回消费 `turns`）SHALL 仅消费 `user_query`/`final_sql`，不受展示数据迁移影响。

#### Scenario: history_cache 复用判断不受影响
- **WHEN** 新会话查询完成，事件流写入 `event_cache` 而非 `session_memory` 的事件流字段
- **THEN** `history_cache` 仍从 `session_memory.conversation_history` 读取 `user_query`/`final_sql` 做复用判断
- **AND** 历史复用命中率与行为与本改动前一致

#### Scenario: 混合召回不受影响
- **WHEN** 新会话查询完成
- **THEN** `JsonConversationStore` 仍按原逻辑向 `turns` key 写入 6 个固定字段
- **AND** 混合召回（dense + BM25 + RRF）行为与本改动前一致

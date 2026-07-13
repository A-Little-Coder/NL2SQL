## ADDED Requirements

### Requirement: 前端停止按钮在在途流期间可终止请求

`Conversation` SHALL 在 `sending`（初始查询流式）或 Turn `streaming` 态期间显示"停止"按钮。点击停止按钮 SHALL 调用 `useQueryStream.cancel()`（abort 底层 fetch）并调用 `cancelTurn(turnId)` 使 Turn 进入 `cancelled` 终态。Turn 处于 `awaiting_clarification`（反问等待，无在途流）时 MUST NOT 显示停止按钮。

#### Scenario: 在途请求点击停止
- **WHEN** 用户发起查询，Turn 处于 `streaming`，用户点击"停止"
- **THEN** 前端 abort SSE fetch
- **AND** Turn 状态转为 `cancelled`，时间轴追加"用户已取消"节点
- **AND** 不再显示"推理进行中…"loading

#### Scenario: 反问等待态不可停止
- **WHEN** Turn 处于 `awaiting_clarification`（反问气泡展示中）
- **THEN** 停止按钮不显示（或禁用）
- **AND** 用户只能回答反问或发起新查询

#### Scenario: resume 续流期间停止按钮可见
- **WHEN** 用户回答反问触发 `sendResume` 续流，Turn 经 `resumeTurn` 置回 `streaming`（此阶段 `Conversation` 的 `sending` 为 false，因 resume 不经过 `handleSend`）
- **THEN** 停止按钮 SHALL 显示（以 Turn `streaming` 态为准，而非 `sending`）
- **AND** 点击停止能 abort resume 的 fetch 并 `cancelTurn`

### Requirement: cancelled 为 Turn 终态且不复用僵尸 streaming

`TurnStatus` SHALL 新增 `'cancelled'` 终态。`cancelTurn(turnId)` SHALL 将 Turn 置为 `cancelled`，向 timeline 追加节点 `{type:'error', status:'done', summary:'用户已取消'}`，置 `cancelled=true`、`rejection=false`、`error='用户已取消请求'`。`cancelled` Turn MUST NOT 显示"推理中"loading，MUST NOT 渲染结果表。abort fetch 后若未显式 `cancelTurn`，Turn MUST NOT 停留在 `streaming`（`useQueryStream.cancel` SHALL 调用 `cancelTurn`）。

#### Scenario: 取消后 Turn 进入 cancelled 终态
- **WHEN** `cancelTurn` 被调用
- **THEN** `turn.status='cancelled'`，`turn.cancelled=true`
- **AND** 时间轴含"用户已取消"节点
- **AND** AssistantCard 显示"已取消"提示，不显示 loading 与结果表

#### Scenario: cancelled 与 error 视觉区分
- **WHEN** Turn 为 `cancelled`（非真错误）
- **THEN** 提示文案为"已取消"而非"处理出错"
- **AND** `rejection=false`，不显示拒答样式

#### Scenario: 取消后时间轴 active 节点停止旋转
- **WHEN** 取消时时间轴存在 `active` 态节点（正在执行的节点）
- **THEN** `cancelTurn` SHALL 将这些节点置为 `cancelled` 态（灰色图标、停止旋转）
- **AND** 时间轴 MUST NOT 残留 `active` 态节点

### Requirement: 后端节点边界合作式取消

`query.py` 的 `run_graph` SHALL 在 `graph.stream(...)` 循环每次处理完 update 后检查 `cancel_event`（`threading.Event`）；`cancel_event.is_set()` 时 SHALL `break` 退出循环。由于 `run_single_query` 节点 invoke 子图期间主图 stream 阻塞、看不到子图内部节点边界，`cancel_event` SHALL 同时经 ContextVar（`current_cancel_event`）注入，`_wrap_node` 在每个节点（含子图 ir/ss/cg/execution/decision）开始前检查，set 则抛 `CancelRequested` 异常终止图；`run_graph` SHALL 识别 `CancelRequested` 不 emit error。取消 SHALL 仅在节点边界生效--当前正在执行的节点（含进行中的 LLM 调用）SHALL 跑完后才退出。

#### Scenario: 取消信号在节点边界生效
- **WHEN** `cancel_event` 被置位，且后台线程正在执行节点 N
- **THEN** 节点 N 执行完成后，循环 break，不再执行节点 N+1
- **AND** 后台线程退出，queue 收到 sentinel

#### Scenario: 取消在子图节点边界生效
- **WHEN** `run_single_query` invoke 子图执行期间客户端取消（`cancel_event` 置位）
- **THEN** 子图下一个节点开始前 `_wrap_node` SHALL 检测到取消并抛 `CancelRequested`
- **AND** 图终止，不必等整个 `run_single_query`（含所有子图节点）完成
- **AND** `run_graph` 识别 `CancelRequested`，不 emit error 事件

#### Scenario: 未取消时零影响
- **WHEN** 请求正常完成，`cancel_event` 从未被置位
- **THEN** `graph.stream` 循环正常跑完所有节点
- **AND** 行为与本次改动前完全一致

### Requirement: 后端检测客户端断开并触发取消

`event_stream` SHALL 在 SSE 心跳超时分支调用 `request.is_disconnected()`，返回真时 SHALL `cancel_event.set()` 并退出循环。`event_stream` SHALL 捕获 `asyncio.CancelledError` / `GeneratorExit`（客户端关闭连接时 Starlette 触发），在捕获处 `cancel_event.set()`。`finally` 块 SHALL 兜底 `cancel_event.set()` 以确保后台线程停止。

#### Scenario: 客户端主动 abort 触发取消
- **WHEN** 前端 abort fetch，客户端连接关闭
- **THEN** 后端 `event_stream` 在下次 yield/心跳时感知断开
- **AND** `cancel_event.set()`，后台线程在当前节点边界退出

#### Scenario: 心跳间隔内检测断开
- **WHEN** 客户端断开且无新事件，心跳超时分支执行
- **THEN** `request.is_disconnected()` 返回真
- **AND** 置位 `cancel_event` 并退出 SSE 循环

### Requirement: 取消后跳过 result/done 推送并释放资源

`event_stream` 在 `cancel_event` 触发的 `break` 后 SHALL 跳过 `result` / `done` SSE 事件推送（连接已断，无人接收）。`finally` 块 SHALL 调用 `pool.release(db_id)` 释放数据库引用计数，无论取消与否。后台线程退出后 SHALL 不再 emit 任何事件。

#### Scenario: 取消后不推 result/done
- **WHEN** 因取消 break 退出 SSE 循环
- **THEN** 不推送 `result` 与 `done` 事件
- **AND** 前端 Turn 由 `cancelTurn` 置终态，不依赖 done 事件

#### Scenario: 取消后释放 db_ctx
- **WHEN** 请求被取消
- **THEN** `finally` 执行 `pool.release`，db_ctx 引用计数递减
- **AND** 不长持有数据库资源

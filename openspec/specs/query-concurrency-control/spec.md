## Purpose

定义 `/query` 查询的并发控制：限制同时在飞的查询数、排队公平性、排队超时与可观测，保护大模型 API 并发上限（按 API Key 限并发）不被打穿。依赖"单条查询内部 LLM 调用串行"不变量（请求级并发 ≈ LLM 级并发，1:1），故请求级闸等价 LLM 级闸。

## Requirements

### Requirement: 全局并发查询上限

系统 SHALL 限制同时在飞的 `/query` 图执行数量上限为 `QUERY_MAX_CONCURRENCY`（env 可配，默认 4）。该上限为**全局**维度（跨所有会话、用户、db 共享），因后端共用单一 API Key。在飞数量未达上限时，新请求 SHALL 立即开始执行；已达上限时，新请求 SHALL 进入排队等待（MUST NOT 以 5xx 拒绝），待有槽位释放后按顺序开始执行。

#### Scenario: 未达上限立即执行

- **WHEN** 当前在飞查询数 < `QUERY_MAX_CONCURRENCY`，新 `/query` 请求到达
- **THEN** 该请求立即开始执行图，不等待
- **AND** 不产生 `queued` 事件

#### Scenario: 达上限排队不拒绝

- **WHEN** 当前在飞查询数已达 `QUERY_MAX_CONCURRENCY`，新 `/query` 请求到达
- **THEN** 该请求进入排队等待，HTTP 连接保持打开
- **AND** 服务 MUST NOT 返回 5xx 或关闭连接
- **AND** 待任一在飞查询完成释放槽位后，该排队请求开始执行

### Requirement: 排队公平性

排队中的查询 SHALL 以 **FIFO**（先到先得）顺序获取释放的槽位，MUST NOT 出现后到请求插队先执行。

#### Scenario: 超额请求按到达顺序执行

- **WHEN** 上限为 1，请求 A、B、C 依次到达（A 立即执行，B、C 排队），A 完成释放槽位
- **THEN** B 先于 C 开始执行
- **AND** C 在 B 完成后才开始

### Requirement: 排队状态可见

请求处于排队态（尚未开始执行图）时，系统 SHALL 通过 SSE 推送 `queued` 事件给客户端，并依赖既有心跳保活连接。客户端 SHALL 能据此展示"排队中"提示。

#### Scenario: 排队请求收到 queued 事件

- **WHEN** 请求因达上限进入排队
- **THEN** SSE 流先推送 `queued` 事件（payload 含 `query_id`）
- **AND** 连接通过心跳保持，直至获得槽位后开始推送正常 stage 事件

#### Scenario: 未排队不推送 queued

- **WHEN** 请求未达上限、立即执行
- **THEN** SSE 流 MUST NOT 推送 `queued` 事件

### Requirement: 排队超时与繁忙提示

请求排队等待超过 `QUERY_QUEUE_TIMEOUT`（env 可配，默认 60 秒）仍未获得槽位时，系统 SHALL 终止该请求的等待，并通过 SSE 推送 `error` 事件（payload 含 `query_id`、`queue_timeout: true`、错误消息"排队超时，当前服务繁忙，请稍后重试"），随后推送 `done` 事件并关闭流。系统 MUST NOT 让请求无限期排队。超时发生时该请求尚未获得槽位，MUST NOT 持有或释放并发闸槽位。

#### Scenario: 排队超时返回繁忙提示

- **WHEN** 请求排队等待时长超过 `QUERY_QUEUE_TIMEOUT` 仍未获得槽位
- **THEN** SSE 流推送 `error` 事件，消息为"排队超时，当前服务繁忙，请稍后重试"且 `queue_timeout=true`
- **AND** 随后推送 `done` 事件并关闭流
- **AND** 该请求 MUST NOT 持有并发闸槽位

#### Scenario: 超时前获得槽位正常执行

- **WHEN** 请求排队等待时长未超过 `QUERY_QUEUE_TIMEOUT` 即获得槽位
- **THEN** 请求正常开始执行图，MUST NOT 推送超时 `error` 事件

### Requirement: 并发闸可观测

`GET /api/v1/health` SHALL 在响应中暴露并发闸状态：当前在飞查询数（`in_flight`）与当前排队等待数（`waiting`）。

#### Scenario: health 返回并发计数

- **WHEN** 有 2 个查询在飞、1 个排队时调用 `/api/v1/health`
- **THEN** 响应含并发闸状态字段，`in_flight=2`、`waiting=1`
- **AND** 无查询时 `in_flight=0`、`waiting=0`

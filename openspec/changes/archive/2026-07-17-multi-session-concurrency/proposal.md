## Why

当前前台是"单在途轮次 + 单活动会话"模型：`Conversation` 单实例挂载，局部 `sending` 态在整条 SSE 流期间为 `true` 且不随会话切换重置，导致一个会话跑问数时其他会话输入框被禁用、`handleSend` 被早退拦截。而后端实际已是 `async` + 一查询一线程架构（`run_in_executor`），天然支持跨会话并行--瓶颈仅在前台 UI 层。本变更放开跨会话真并行，并在后端加进程内并发闸保护大模型限流，使"多会话同时问数"端到端可用。

## What Changes

- **前台并行化**：`sending` 从单实例局部态改为按会话/按 turn 跟踪；`useQueryStream` 的单值 `abortRef`/`turnIdRef` 改为 `Map<turnId, AbortController>` 支持多在途流；SSE reducer 支持向非当前会话的 turns 持续 reduce（后台会话流不在前台显示也能更新）；侧栏显示每会话运行态。
- **后台并发闸**：在 `/query` endpoint 加进程内 `asyncio.Semaphore` 请求级并发闸（不改动 graph / `LLMClient` 内部结构），超过上限的查询排队、FIFO 公平；新增 `queued` SSE 事件；排队超时（默认 60s）返回 `error` 事件提示"排队超时，当前服务繁忙，请稍后重试"；`/health` 暴露在跑/排队计数。
- **共享状态线程安全加固**：验证并必要时加锁 `event_cache` / `session_memory` / `history_cache` 等共享可变状态，支撑多线程并发写。
- **教程**：`learn/` 下并发闸原理教程 demo（信号量 → 公平性 → 可观测）。

## Capabilities

### New Capabilities

- `query-concurrency-control`: 限制同时在飞的 `/query` 数，超额排队、FIFO 公平、可观测；保护大模型并发上限不被打穿。依赖"单条查询内部 LLM 调用串行"不变量（请求级并发 ≈ LLM 级并发，1:1）。

### Modified Capabilities

- `frontend-ui`: 从"全局单在途轮次"改为"跨会话并行、单会话内仍单在途"；输入/发送/取消按会话维度独立，切换会话不重置在途态。

## Impact

- **前台**：`frontend/src/components/Conversation/`、`hooks/useQueryStream.ts`、`store/useChatStore.ts`、`store/reducer.ts`、`components/SessionSidebar/`。
- **后台**：`src/api/routes/query.py`（endpoint 闸 + `queued` 事件）、`src/api/deps.py`（Semaphore 单例 + 计数）、`src/api/app.py`（`/health` 暴露计数）、可能 `src/memory/event_cache.py` / `session_memory.py` / `history_cache.py`（线程安全加固）。
- **依赖**：无新增外部依赖（不引入 Redis/MQ；进程内 `asyncio.Semaphore`）。
- **配置**：新增 `QUERY_MAX_CONCURRENCY` env（默认 4）与 `QUERY_QUEUE_TIMEOUT` env（默认 60s）。
- **不变量**：design.md 显式记录"单条查询内部 LLM 调用保持串行"--这是请求级闸等价于 LLM 级闸的前提；若将来引入查询内并行，需补 LLM 级兜底闸。

## 端到端测试方案（playwright，精简版）

为控制 token 消耗，e2e 只覆盖**一个核心并发场景**，其余边界放后台单测。

**唯一 e2e 用例：跨会话并发不互斥**
- 前置：后端已起、已选库；`QUERY_MAX_CONCURRENCY ≥ 2`。
- 步骤：会话 A 发一条较慢的问数（触发完整 pipeline）；A 进入 streaming 后，切到会话 B，输入并发送一条问数。
- 断言：① A streaming 期间 B 输入框可输入、发送钮可点；② A、B 两条流独立完成、各自出结果；③ 侧栏 A、B 均显示过"运行中"态。
- 不做（省 token，转单测）：排队阈值边界、`queued` 事件、超时、并发取消--这些用后台 pytest + 直接调 endpoint 验证。

**后台单测补充**（非 e2e，低成本）：Semaphore 并发计数正确性、超额排队 FIFO、`/health` 计数、共享状态并发写不冲突。

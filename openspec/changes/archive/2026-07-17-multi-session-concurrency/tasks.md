# Implementation Tasks

按 design.md 三阶段顺序执行；阶段一（后台）可独立验证，阶段二（前台）依赖阶段一的闸与线程安全加固。

## 1. 后台并发闸（阶段一）

- [x] 1.1 在 `src/api/deps.py` 构造进程单例 `query_sem`：`asyncio.Semaphore(QUERY_MAX_CONCURRENCY)`，包一层计数器暴露 `in_flight`/`waiting`，`QUERY_MAX_CONCURRENCY` 从 env 读取（默认 4），`QUERY_QUEUE_TIMEOUT` 从 env 读取（默认 60s）
- [x] 1.2 在 `src/api/routes/query.py` 的 `event_stream`：排队态 push `queued` SSE 事件（含 `query_id`）；`await asyncio.wait_for(query_sem.acquire(), timeout=QUERY_QUEUE_TIMEOUT)` 获取槽位，try-finally 包裹 `run_in_executor` 确保获槽位后释放
- [x] 1.3 排队超时：`wait_for` 抛 `TimeoutError` 时 push `error` 事件（`queue_timeout: true`，消息"排队超时，当前服务繁忙，请稍后重试"）+ `done` 关闭流；未获槽位不释放
- [x] 1.4 `/health`（`src/api/app.py`）响应新增并发闸状态字段 `concurrency: {in_flight, waiting, max}`
- [x] 1.5 在 `LLMClient` / graph 节点 / `single_query_graph` 顶部注释标注 D2"查询内 LLM 串行"不变量，提示未来引入并行时须补 LLM 级闸

## 2. 共享状态线程安全 spike 与加锁（阶段一）

- [x] 2.1 spike：`event_cache`（`store_turn_events`/`clear_pending` 文件写）多线程并发写是否冲突，记录结论
- [x] 2.2 spike：`session_memory.add_turn` 与 `get_recent_turns` 并发读写是否安全，记录结论
- [x] 2.3 spike：`history_cache` 并发访问是否安全，记录结论
- [x] 2.4 依 spike 结论对需加锁处加 `threading.Lock`（最小范围，避免无谓降并发）；无需加锁处注释说明依据

## 3. 后台测试（阶段一）

- [x] 3.1 pytest：Semaphore 并发计数正确性（N 并发请求恰好 N 在飞）
- [x] 3.2 pytest：超额请求 FIFO 排队、按序获取槽位
- [x] 3.3 pytest：`/health` `in_flight`/`waiting` 计数正确
- [x] 3.4 pytest：`queued` 事件仅在排队时推送、未排队不推送
- [x] 3.5 pytest：排队超时返回 `error`（`queue_timeout: true`）+ `done`，且未获槽位不释放；超时前获槽位正常执行不报超时
- [x] 3.6 pytest：共享状态并发写不冲突（依 2.x spike 结论构造并发用例）
- [x] 3.7 既有 e2e/单测全绿（闸对单在途查询透明，无回归）

## 4. 前台跨会话并行化（阶段二）

- [x] 4.1 `store/useChatStore.ts`：新增按会话/turn 在途跟踪（如 `runningTurnIds` 或 `turnsBySession` 内 turn 状态），移除对 `Conversation` 局部 `sending` 的全局依赖
- [x] 4.2 `hooks/useQueryStream.ts`：`abortRef`/`turnIdRef` 单值改 `Map<turnId, AbortController>`；`cancel(turnId)` 精确 abort；`sendQuery` 不再阻塞全局输入
- [x] 4.3 `store/reducer.ts` + `applyEvent`：SSE `onEvent` 按 `sessionId` 路由 reduce 进 `turnsBySession[sessionId]`，仅当前会话同步到展示 `turns`（reducer 纯函数零改动）
- [x] 4.4 `components/Conversation/index.tsx`：输入框/发送钮 `disabled` 改判"当前会话是否有在途 Turn"；同会话在途时发送变"停止"
- [x] 4.5 `components/SessionSidebar/`：为有在途 Turn 的会话展示运行态指示，终态清除
- [x] 4.6 消费 `queued` 事件：排队中会话展示"排队中…"提示；消费排队超时 `error` 事件（`queue_timeout: true`）展示"排队超时，当前服务繁忙，请稍后重试"

## 5. 前台测试（阶段二）

- [x] 5.1 Vitest：`applyEvent` 按 session 路由、后台会话流持续更新、切回见推进态
- [x] 5.2 Vitest：多流 `cancel(turnId)` 隔离，取消 A 不影响 B
- [x] 5.3 Vitest：同会话单在途拦截、跨会话不互斥
- [x] 5.4 精简 playwright e2e（唯一用例：跨会话并发不互斥）：A streaming 期间切 B 可输入发送，两会话独立完成、侧栏运行态可见
- [x] 5.5 既有前端测试全绿（`npm run test`）

## 6. 并发闸教程 demo（阶段三）

- [x] 6.1 `learn/concurrency-gate/`：信号量限流原理可运行 demo（threading/asyncio Semaphore 对比）
- [x] 6.2 demo：两级闸（请求级 vs LLM 级）与 1:1 不变量演示
- [x] 6.3 demo：FIFO 公平性 vs 非公平、排队可观测
- [x] 6.4 附详细说明文档（新手向，逐步拆解）

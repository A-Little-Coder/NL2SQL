## Context

当前前台为"单在途轮次 + 单活动会话"模型：`Conversation` 单实例挂载（`AppLayout.tsx:196`），局部 `sending` 态在整条 SSE 流期间为 `true` 且不随会话切换重置（无 `currentSessionId` 监听清空），导致一会话跑问数时其他会话输入框禁用、`handleSend` 早退。后台实则已 `async` + 一查询一线程（`run_in_executor`），天然支持跨会话并行--瓶颈仅在前台 UI 层。

关键事实（已核实，决定方案走向）：
- 单查询图是线性链（`single_query_graph.py`：`ir→ss→permission→schema_finalize→answerability→cg→execution→decision→mask`），节点串行。
- 候选生成是**单次** LLM 调用（`sql_generator.py:262`，一次返回 N 候选），decision R1/R2 各一次串行调用，多意图为串行编排。
- 全 `src/` 搜 `ThreadPoolExecutor`/`threading.Thread`/`asyncio.gather`/`concurrent.futures`/`.submit()`/`.map()` **零命中**--业务逻辑无查询内并行。
- 因此：**并发查询数 N ≈ 并发 LLM 调用数 N（1:1）**。请求级限流即等价于 LLM 级限流。

约束：`run_api.py:22` 明确"当前仅支持单 worker"；`Globals`（BGE/VectorStore/LLM 单例）、`InMemorySaver` checkpointer、`DbContextPool` 均为进程内单例。

## Goals / Non-Goals

**Goals:**
- 前台放开跨会话真并行：一会话 streaming 不阻塞其他会话输入/发送。
- 后台加进程内并发闸，保护大模型并发上限不被打穿（防 429）。
- 闸不动 graph / `LLMClient` 内部结构，仅挂 endpoint 边界。
- 共享可变状态在多线程并发写下不冲突。
- 提供 `learn/` 并发闸原理教程 demo。

**Non-Goals:**
- 不引入 Redis / RabbitMQ 等外部 MQ 基础设施（不做多 worker 水平扩容）。
- 不改 `LLMClient` 内部（不加 LLM 级 `threading.Semaphore`）--留作未来查询内并行化时的兜底。
- 不做同会话内并发（单会话仍单在途）。
- 不做 per-user / per-db 细粒度配额（v1 全局闸）。

## Decisions

### D1: 并发闸用进程内 `asyncio.Semaphore` 挂在 `/query` endpoint，不用外部 MQ，不改 LLMClient

**选择**：在 `event_stream` 开头 `async with query_sem` 包裹 `run_in_executor`，`query_sem = asyncio.Semaphore(QUERY_MAX_CONCURRENCY)` 为进程单例（`deps.py` 构造）。

**为何不外部 MQ**：外部 MQ + 多 worker 进程会触发连锁重写--`Globals` 单例每 worker 重载（BGE-M3 内存爆炸）、`InMemorySaver` 跨进程 resume 断裂、`DbContextPool` 状态分裂、SSE 需 Redis pub/sub 桥回客户端。为"一台机几个会话并行"上水平扩容，杀鸡用牛刀且高风险。

**为何不改 LLMClient 内部**：因 1:1 不变量（见 D2），请求级闸已等价于 LLM 级闸。挂 endpoint 零内部耦合，且 `asyncio.Semaphore`（Py3.10+ FIFO 公平）比 `threading.Semaphore`（不保证公平）更优。原语选择：endpoint 是 async 上下文，用 `asyncio.Semaphore` 正确；若挂在 `LLMClient`（sync 线程）则须用 `threading.Semaphore`--现选前者故用 asyncio 的。

**为何不用有界 executor 替代**：`loop.set_default_executor(ThreadPoolExecutor(max_workers=K))` 也能限并发，但 executor 内部队列无界、无排队可见性、无 `queued` 事件能力。Semaphore 方案可观测、可发 `queued` 事件、FIFO 公平，更贴合需求。

### D2: 请求级闸等价 LLM 级闸，依赖"查询内串行"不变量

1:1 成立的前提是单条查询任意时刻最多 1 次 LLM 调用在飞。该不变量当前成立（D1 已核实）。**此不变量须在代码与文档中显式标注**：若将来引入查询内并行（如并行候选生成、并行子图），请求级闸会静默欠数、可能重新 429，届时须补 LLM 级 `threading.Semaphore` 兜底。

### D3: 前台 `sending` 改按会话/turn 跟踪，`abortRef` 改 Map

- store 新增 `runningTurnIds: Set<string>`（或按 session 索引），取代 `Conversation` 局部 `sending`。输入框/发送钮 `disabled` 改为判断"**当前会话**是否有在途 Turn"，而非全局 `sending`。
- `useQueryStream` 的 `abortRef`/`turnIdRef` 单值 ref 改为 `Map<turnId, AbortController>`，支持多在途流；`cancel(turnId)` 精确 abort 对应流。
- 同会话单在途：当前会话已有在途 Turn 时发送被拦截、按钮变"停止"（沿用现有 `canStop` 逻辑但限定到当前会话）。

### D4: 后台会话流持续 reduce（applyEvent 按 session）

现状 `applyEvent(turnId, event)` 写"当前 turns"。改造为按 session 路由：SSE `onEvent` 回调持有该流所属 `sessionId`，事件 reduce 进 `turnsBySession[sessionId]` 对应 turn；仅当 `sessionId === currentSessionId` 时同步反映到展示用 `turns`。切回后台会话时从 `turnsBySession` 加载已推进态。reducer 纯函数零改动。

### D5: 同会话单在途由前台强制；后台同会话竞态列为风险

前台强制单会话单在途（D3）。后台 `InMemorySaver` 以 `thread_id=session_id` 为 checkpoint key，同 session 并发 `.stream` 会竞态--v1 以前台拦截为唯一防线，不新增后台 409 拒绝（避免过度设计）。若未来要去掉前台单会话限制，须先加后台同会话串行化。

### D6: `queued` 事件 + 排队超时 + `/health` 可观测

- 排队态（`await query_sem.acquire()` 前）推送 `queued` SSE 事件（含 `query_id`），心跳保活。
- 排队超时：`await asyncio.wait_for(query_sem.acquire(), timeout=QUERY_QUEUE_TIMEOUT)`（默认 60s，env 可配）。超时抛 `TimeoutError` -> 推送 `error` 事件（`queue_timeout: true`，消息"排队超时，当前服务繁忙，请稍后重试"）+ `done` 关闭流。因 `wait_for` 超时即未获槽位，无需 release。前台沿用既有 `error` 渲染展示该消息（turn 进 error 终态，会话运行态指示同步清除）。
- `query_sem` 包一层计数器（`in_flight`/`waiting`），`/health` 暴露。看不见的闸等于没有。

### D7: 共享状态线程安全加固（spike 驱动）

前台放开并发后，`event_cache`（写事件流文件）、`session_memory`（`add_turn`）、`history_cache` 会被多线程并发写。须 spike 验证：文件写是否需加 `threading.Lock`、`OrderedDict`/list 操作是否原子。按 spike 结果决定加锁范围，避免无谓加锁降并发。

**Spike 结论（任务 2.1-2.3）：**
- `storage.py`：已有文件级锁（`.lock`+O_EXCL）+ 原子写（tmp+replace），**单次写防撕裂**；但 `atomic_read` 不加锁，read-modify-write 序列**不防丢更新**。
- `event_cache`：`store_turn_events`/`register_session`/`clear_pending`/`delete_session` 均为 index.json + 会话文件的 read-modify-write。**同用户跨会话并发会丢更新**（一个会话的 index 条目被覆盖）。**已加按用户 `RLock`**（`_user_lock`），跨用户不互斥；`store_turn_events` 重入 `register_session` 故用 RLock。读方法（`list_sessions_paged`/`get_session_events`）不加锁（stale-but-consistent，atomic_read 读完整文件）。
- `session_manager`：`_cache`(dict)+`_access_order`(list) 共享无锁，`_update_access` 的 `in`+`remove` 竞态可抛 ValueError。**已加 `RLock`** 包裹 4 个 cache helper。
- `session_memory`：按 session 分实例（缓存复用）+ 分文件（`sessions/{user}/{session_id}.json`）。跨会话写不同文件安全；同会话并发依赖 **D5 前端单会话单在途**。**无需加锁**（依 D5）。
- `history_cache`：只读/无状态（无共享可变写）。**无需加锁**。

## Risks / Trade-offs

- **[风险] 查询内并行化使 1:1 失效 -> 重新 429** -> D2 不变量显式标注 + design 记录；未来引入并行时补 LLM 级闸。
- **[风险] 同会话并发打竞态 checkpoint** -> D5 前台强制单会话单在途；列为已知限制。
- **[风险] 排队过久** -> D6 排队超时（默认 60s）终止并返回繁忙提示，前台展示"排队超时，当前服务繁忙"；`waiting` 计数暴露堆积供监控调参。
- **[权衡] 全局闸可能被单用户洪流占满** -> v1 接受；per-user 配额留作未来。
- **[权衡] `queued` 期间不占 worker 线程（async 等待）** -> 正面：排队廉价；负面：极端堆积下连接数上升，靠 `waiting` 计数监控。
- **[风险] 共享状态并发写未覆盖** -> D7 spike 先行，加锁以 spike 结果为准。

## Migration Plan

分两阶段，后台先行可独立验证：

1. **阶段一（后台）**：加 `query_sem` + `queued` 事件 + `/health` 计数；D7 共享状态 spike 与加锁。后台单测 + 既有 e2e 回归（前台未改，单在途行为不变，闸对单查询透明）。
2. **阶段二（前台）**：D3/D4/D5 前台并行化改造；精简 playwright 并发 e2e。
3. **阶段三（教程）**：`learn/` 并发闸 demo。

**回滚**：阶段一闸默认 `QUERY_MAX_CONCURRENCY` 设大于实际并发即等价无闸；阶段二前台改造可 feature flag 回退到单在途模型。

## Open Questions

- `QUERY_MAX_CONCURRENCY` 已设为 4（保守值，env 可调）；若实测大模型套餐并发上限更高可调大。
- `QUERY_QUEUE_TIMEOUT` 默认 60s 是否合适？上线后依 `waiting` 监控数据调整。
- D7 spike 后若 `event_cache` 写入需加锁，是否影响既有单查询路径性能（锁竞争）？以 spike 数据为准。

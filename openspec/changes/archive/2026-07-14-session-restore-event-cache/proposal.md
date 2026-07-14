## Why

前台在切换不同会话后再切回，原会话的对话、时间轴、结果及其他细节全部丢失，只能看到残缺摘要。根因是三重断点：① 后端 `session_memory`（决策 28）只持久化摘要字段（`user_query`/`final_sql`/`result_meta`），时间轴节点、SQL 候选、真实行数据从未存过；② 前端 store 只持有单个 `turns` 数组，切换会话时直接覆盖、无按会话缓存；③ `setHistoryTurns` 用简化重建抹掉了仅有的一点信息（`rows` 填空对象、时间轴只剩 1 个节点）。

调研后发现一个关键事实：**前端展示的所有中间过程数据，本质是 SSE 事件 payload 的累积**，而 SSE 事件本身就是 JSON。因此这些数据天然可序列化，且可通过"存事件流、前端重放"实现 100% 可恢复（除结果行数据按 20 行采样）。这绕开了 `SQLCandidate`/`DecisionResult` 等后端对象的序列化难题——前端展示的从来不是这些对象，而是它们在发 SSE 时已转好的精简 payload。现在做是因为用户报告了该体验问题，且事件流重放方案让"完整恢复"首次变得低成本、零漂移。

## What Changes

- **新增 `event_cache` 展示存储层**（项目根 `event_cache/`，与 `data/` 平级，运行时数据纳入 `.gitignore`）：按 `{user_id}/shard_xxxx/{session_id}.json` 三级组织，按 `created_at` 分片，每 shard 目录 ≤20 会话、每会话一文件存该会话全部 turn 的 SSE 事件流。与 `session_memory`（复用层）物理分离，互不影响。
- **后端捕获事件流并持久化**：`query.py` 在 SSE 生成器内统一捕获已发出的所有事件（graph 节点 emit 的 + 自构造的），`turn done` 时写入对应会话的 `event_cache` 文件；`result` 事件的 `rows` 在存储层截断为前 20 行（实时推给前端的仍为完整行）。
- **resume 跨阶段缓冲**：反问场景下 `query`（挂起）与 `resume`（完成）跨两次请求，按会话级事件缓冲累积两阶段事件流，`resume done` 时落盘为完整事件流。
- **新增 `event_cache` 读写 API**：会话列表按 shard 分页（惰性加载，每页 ≤20 会话）；会话事件流读取接口。`create_session` 双写——`session_memory` 建会话 + `event_cache/{uid}/index.json` 登记摘要（`session_id`/`created_at`/`turn_count`/`updated_at`/`status`/`shard_id`）。
- **前端重放恢复**：`setHistoryTurns` 改为把事件流喂给现有 `reduceSseEvent` 重放，reducer 本身零改动，重放出的 `Turn` 与实时累积的完全一致；新增 `turnsBySession` 内存缓存，同次浏览器会话内切回优先用缓存（完整行兜底）。
- **SessionSidebar 无限滚动**：左栏从全量渲染改为按 shard 分页惰性加载（初始最新 shard，下拉加载更早 shard）。
- **老会话兼容**：无事件流的存量会话回落 `session_memory` 摘要简化重建（与现状一致），新会话自然完整恢复。
- **`session_memory` 复用层不动**：`conversation_history`/`turns` 继续服务 `history_cache` 复用判断与混合召回，`history_cache` 两条读取路径均只消费 `user_query`/`final_sql`，展示数据迁出后零影响。

## Capabilities

### New Capabilities

- `session-display-restore`: 会话切换后的展示恢复能力——`event_cache` 展示存储层（按 `created_at` 分片、shard 目录、每会话一文件存事件流）、SSE 事件流持久化与重放、`result` 行数据 20 行采样、resume 跨阶段事件缓冲、会话列表分页惰性加载、`turnsBySession` 内存缓存、老会话摘要兜底兼容。

### Modified Capabilities

（无。现有 `frontend-ui` spec 未定义会话切换恢复行为，`api-service` spec 未涵盖 sessions/history API；本变更的新行为统一归入新能力 `session-display-restore`，`session_memory` 复用层 spec 无 requirement 变化。）

## Impact

- **后端新增**：`src/memory/event_cache.py`（展示存储层：shard 分配、index 维护、事件流读写、result 截断、resume 缓冲）；`src/api/schemas.py` 增加分页列表与事件流响应类型。
- **后端改动**：`src/api/routes/query.py`（SSE 事件统一捕获 + turn done 写 event_cache + resume 缓冲）、`src/api/routes/session.py`（`create_session` 双写、`list_sessions` 分页化、`get_session_history` 改读 event_cache 事件流、老会话回落）、`src/api/deps.py`（注入 event_cache store）。
- **前端改动**：`frontend/src/store/useChatStore.ts`（`turnsBySession` 缓存 + `setHistoryTurns` 改重放）、`frontend/src/components/SessionSidebar/index.tsx`（无限滚动分页）、`frontend/src/api/rest.ts` + `types.ts`（分页列表 API + 事件流响应类型 + `SessionTurn` 扩展）。
- **存储/配置**：新增 `event_cache/` 目录（`.gitignore`）；`session_memory`（`data/sessions/`）零改动。
- **测试**：后端 pytest（event_cache 读写/分片分配/index 双写/result 截断/resume 缓冲/老会话回落）、前端 vitest（事件流重放等价性、`turnsBySession` 命中与未命中、分页加载）、Playwright E2E（发起查询→切到会话B→切回→断言对话/时间轴/候选/结果均在；下拉加载更早会话）。
- **API 兼容性**：`list_sessions` 增加分页参数（默认返回第一页，向后兼容）；`get_session_history` 响应结构由摘要 `SessionTurn[]` 变为事件流（前端配套改造，老会话回落保持摘要形态）。

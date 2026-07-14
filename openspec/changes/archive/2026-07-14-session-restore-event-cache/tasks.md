## 1. 前置确认

- [x] 1.1 确认 resume graph 机制：读 `src/graph/` resume 路径与 `src/api/routes/query.py` resume 处理段，判断 `query` 阶段（`__interrupted__`）的 `captured_events` 能否在 resume 时获取/合并，确定 D5 跨阶段事件缓冲的具体实现（暂存方案 vs 退化方案）
- [x] 1.2 确认 `list_sessions` 现有调用方，核实新增 `page`/`size` 分页参数的向后兼容性

## 2. 后端 event_cache 存储层

- [x] 2.1 新建 `src/memory/event_cache.py`，实现 `EventCacheStore`：复用 `Storage` 原子写 + 文件锁；定义 `event_cache/{user_id}/shard_xxxx/{session_id}.json` 三级路径与 `index.json` 结构
- [x] 2.2 实现 shard 分配逻辑：按 `created_at` 顺序，每 shard 目录 ≤20 会话，满 20 开新 shard；会话归属 shard 永久不变
- [x] 2.3 实现 `register_session`（登记 `index.json` 摘要 + 建空会话文件）、`append_turn_events`（追加 turn 事件流）、`update_turn_meta`（更新 `index.json` 的 `turn_count`/`updated_at`）
- [x] 2.4 实现存储侧 `result` 事件 `rows` 截断前 20 行逻辑（写入时截断，不影响实时推送）
- [x] 2.5 实现 `list_sessions_paged`（按 shard 分页读 `index.json`，最新 shard 在前）与 `get_session_events`（读单会话事件流）
- [x] 2.6 在 `src/api/deps.py` 注入 `EventCacheStore` 单例

## 3. 后端 schemas

- [x] 3.1 `src/api/schemas.py` 新增分页列表响应类型（`SessionListPageResponse`，含 `page`/`size`/`has_more`/`sessions`）
- [x] 3.2 新增事件流响应类型（`SessionEventsResponse`，含 `session_id`/`turns:[{turn_index, events:[...]}]`，老会话回落为摘要形态）

## 4. 后端 query.py 事件捕获与写入

- [x] 4.1 在 SSE 生成器内统一捕获所有已发出事件到 `captured_events`：graph 转发事件（`query.py:284`）与自构造 `result`/`error`/`done` 均在 yield 前 append
- [x] 4.2 `turn done` 且 `_should_write_session_turn` 通过时，将 `captured_events`（result 已截断）写入 `event_cache`，并 `update_turn_meta`
- [x] 4.3 实现 resume 跨阶段缓冲：`query` 阶段（`__interrupted__`）暂存 `captured_events` 到会话进行中缓冲，`resume` 完成时合并两阶段事件流落盘（依 1.1 结论选择暂存或退化方案）

## 5. 后端 session.py API 改造

- [x] 5.1 `create_session` 双写：`session_memory` 建会话 + `event_cache.register_session`
- [x] 5.2 `list_sessions` 分页化：新增 `page`/`size` 参数（默认第一页），改读 `event_cache` `index.json` 分页返回
- [x] 5.3 `get_session_history` 改造：有 `event_cache` 事件流返回事件流，无则回落 `session_memory` 摘要（老会话兼容）
- [x] 5.4 `delete_session` 同步清理 `event_cache`（`index.json` 移除 + 删 shard 文件）

## 6. 前端契约层

- [x] 6.1 `frontend/src/api/types.ts` 扩展：`SessionTurn` 补全字段、新增事件流响应类型与分页列表响应类型
- [x] 6.2 `frontend/src/api/rest.ts`：`listSessions` 加分页参数、`getSessionHistory` 适配事件流响应

## 7. 前端 store 改造

- [x] 7.1 `useChatStore` 新增 `turnsBySession: Record<sessionId, Turn[]>` 缓存字段与存取方法
- [x] 7.2 `setHistoryTurns` 改为事件流重放：有事件流则逐事件 `reduceSseEvent` 重放（reducer 零改动），无则摘要简化重建（兼容两源）
- [x] 7.3 `SessionSidebar.handleSelect` 切换前存当前 `turns` 到 `turnsBySession`，切回时缓存命中优先用缓存（完整行），未命中走重放

## 8. 前端 SessionSidebar 无限滚动

- [x] 8.1 改造为分页惰性加载：初始加载第一页，滚动到底触发加载下一页
- [x] 8.2 处理加载态、空态、`has_more` 边界

## 9. 前端历史快照提示

- [x] 9.1 `ResultTable` 在历史重放来源且结果为截断的 20 行时，显示"历史快照·前20行"提示

## 10. 后端测试（pytest）

- [x] 10.1 `EventCacheStore` 单测：shard 分配（满 20 开新 shard）、`index.json` 读写、事件流追加、result 截断 20 行
- [x] 10.2 resume 跨阶段缓冲单测：query + resume 事件合并完整性
- [x] 10.3 `session.py` API 测试：create 双写、list 分页、history 事件流/老会话回落、delete 清理
- [x] 10.4 `history_cache` 零影响回归测试：复用判断与混合召回行为不变
- [x] 10.5 全部后端测试运行通过（`pytest`）

## 11. 前端测试（vitest）

- [x] 11.1 事件流重放等价性测试：重放重建的 Turn 与实时累积的 Turn 全字段一致
- [x] 11.2 `turnsBySession` 缓存命中/未命中分支测试
- [x] 11.3 `setHistoryTurns` 两源兼容（事件流 vs 摘要）测试
- [x] 11.4 全部前端测试运行通过（`npm run test`）

## 12. Playwright E2E 测试

- [x] 12.1 编写 E2E 用例：发起查询 -> 切到会话 B -> 切回 -> 断言对话/时间轴全部节点/SQL 候选/结果均在
- [x] 12.2 E2E 用例：左栏下拉加载更早会话分页
- [x] 12.3 E2E 用例：刷新页面后切回历史会话，验证重放恢复 + 20 行快照提示
- [x] 12.4 用 playwright 插件执行全部 E2E 用例并通过

## 13. 配置与收尾

- [x] 13.1 `.gitignore` 加入 `event_cache/`
- [x] 13.2 更新 README/文档说明 `event_cache` 展示层用途与结构
- [x] 13.3 `openspec validate session-restore-event-cache --strict` 验证 change 完整性

## Context

当前会话切换恢复残缺，存在三重断点：① 后端 `session_memory`（决策 28）只持久化摘要字段（`user_query`/`final_sql`/`result_meta`/`rejection_reason` 等），时间轴节点、SQL 候选、真实行数据从未存过；② 前端 `useChatStore` 只持有单个 `turns` 数组，切换会话时 `setHistoryTurns` 直接覆盖、无按会话缓存；③ `setHistoryTurns` 用简化重建抹掉信息（`rows` 填 `Array(row_count).fill({})` 空对象、时间轴只剩 1 个 result 节点）。

调研发现关键事实：**前端展示的所有中间过程数据，本质是 SSE 事件 payload 的累积**（`Turn` = 对事件序列跑 `reduceSseEvent` 的结果），而 SSE 事件本身就是 JSON。因此这些数据天然可序列化，可通过"存事件流、前端重放"实现 100% 可恢复。这绕开了 `SQLCandidate`/`DecisionResult`/`RetrievedContext` 等后端对象的序列化难题--前端展示的从来不是这些对象，而是它们在发 SSE 时已转好的精简 payload（如 candidates 是 `{id, sql}[]` 纯字符串，不是 SQLCandidate 对象）。

现有存储现状：`data/sessions/{uid}/{sid}.json` 一个文件内有两套并行 turn 存储--`conversation_history`（`SessionMemory` 维护，给 `history_cache` LLM 判断）与 `turns`（`JsonConversationStore` 维护，给混合召回），靠"整文件读写 + 不同 key"共存。`history_cache` 两条读取路径均只 `.get("user_query")`/`.get("final_sql")`，字段级隔离，对其他字段无感。

## Goals / Non-Goals

**Goals:**
- 切换会话再切回，除结果行数据超过 20 行的部分，其他展示（对话、时间轴全部节点、SQL 候选、检查器细节、思考链、反问上下文、完成元信息）100% 可恢复。
- 展示层与复用层物理分离，`session_memory` 复用链路（`history_cache` 判断 + 混合召回）零影响。
- 大用户（数百会话）首屏快、内存可控：分片 + 惰性加载。
- 零漂移：历史重放路径与实时累积路径共用同一 `reduceSseEvent`，reducer 不改一行。

**Non-Goals:**
- 不改 `session_memory` 复用层语义（决策 28 保持：复用层不存查数结果）。
- 不持久化完整结果行数据（仅前 20 行采样）。
- 不迁移老会话（无事件流的存量会话回落摘要兼容）。
- 不做跨设备同步、服务端实时推送、多端会话合并。
- 不改 graph 执行逻辑（仅在外层 SSE 生成器捕获事件）。

## Decisions

### D1：展示层独立存储 `event_cache/`，与 `data/` 平级
**选择**：项目根新建 `event_cache/`，按 `{user_id}/shard_xxxx/{session_id}.json` 三级组织，与 `data/sessions/` 复用层物理分离。

**理由**：复用层 `session_memory` 服务 `history_cache`，若塞事件流会文件膨胀，且 `list_sessions`/`get_session_history` 读它时要 load 整个大文件。展示数据（事件流，单 turn 可达数十 KB）与复用数据（摘要，几百字节）的读写模式、数据量、生命周期均不同，物理分离是正解。

**备选**：① 塞 `conversation_history` 字段（字段隔离不影响 `history_cache`，但文件膨胀、load 慢）；② 新增 `turn_details` key 同文件（仍膨胀，且 `list_sessions` 全量扫更慢）。均被否。

### D2：存事件流重放，而非存快照或从 state 提取字段
**选择**：后端捕获已发出的 SSE 事件序列存入 `event_cache`；前端 `setHistoryTurns` 改为把事件流喂给现有 `reduceSseEvent` 重放。

**理由**：① 零漂移--SSE 事件是后端→前端唯一契约（`api/types.ts` 已镜像），存事件 = 存契约，reducer 一行不改，重放路径永远与实时路径一致；② 零序列化风险--事件本来就是 JSON 发出去的；③ 100% 可恢复--连 `stage` 进度、`llm_thinking` 文本都能恢复（state 没沉淀的也能，因为事件本身带着）。

**备选**：① 存 Turn 快照（后端构造完整 Turn 结构）--要写"state→Turn"映射，漂移风险高，且要序列化 `SQLCandidate` 等对象；② 从 accumulated state 提取字段存--同样要序列化对象，且 `stage`/`llm_thinking` state 没沉淀，恢复不了。均被否。

### D3：按 `created_at` 分片 + shard 目录 + 每会话一文件
**选择**：分片按会话 `created_at` 顺序（会话创建时分配到当前最新 shard，满 20 开新 shard，归属永久不变）；shard 为目录，内含 ≤20 个会话各一文件。

**理由**：① `created_at` 分片：会话归属 shard 永久不变，写入简单。代价是列表只能"大致降序"（老 shard 里刚更新的会话不会浮顶），可接受；② shard 目录 + 每会话一文件：`turn done` 只读写该会话单文件（轻，几十~几百 KB），分页仍按目录（一次读一个 shard 目录 = 20 会话）。

**备选**：① `updated_at` 分片--会话更新跨 shard 搬移，写入复杂；② 单文件装 20 会话--每次 `turn done` 读写整个 shard 文件（3MB+），重。均被否。

### D4：`result` 行数据存储层截断 20 行
**选择**：`result` 事件写入 `event_cache` 时 `rows` 截断 `[:20]`；实时推给前端的 `result` 事件保留完整行；`turnsBySession` 内存缓存也存完整行。

**理由**：行数据会膨胀 + 会过期（决策 28 的顾虑成立）。20 行预览足够回忆当时查询内容；要精确值用户可重新查询。三层兜底：实时完整 → 内存缓存完整 → 历史重放 20 行 + "历史快照·前20行"提示。

### D5：resume 跨阶段按会话事件缓冲
**选择**：反问场景下，`query`（`__interrupted__` 挂起）阶段的 `captured_events` 暂存到该会话的 `event_cache` 进行中文件（或 `session_memory` 临时字段），`resume` 完成时合并两阶段事件流落盘为完整事件序列。

**理由**：一个 turn 可能跨两次请求，若只在 `resume done` 存 resume 阶段事件，会缺 query 阶段的 keywords/召回/候选。按会话缓冲累积可保证完整。

**待确认**：resume 的 graph 机制（是否从 checkpoint 恢复完整 state、query 阶段事件能否在 resume 时重取/合并）需在实现前确认，见 Open Questions。

### D6：`index.json` 双写会话列表摘要
**选择**：`event_cache/{uid}/index.json` 维护该用户所有会话的摘要（`session_id`/`created_at`/`turn_count`/`updated_at`/`status`/`shard_id`）。`create_session` 双写（`session_memory` 建会话 + `index.json` 登记）；`turn done` 更新 `index.json` 的 `turn_count`/`updated_at`。会话列表分页只读 `index.json`。

**理由**：列表分页只读轻量 index（几十 KB），不全量扫 session 文件。

**备选**：① `list_sessions` 全量读 session 文件后分页--IO 没省；② 列表仍走 `session_memory` 不分页--不满足惰性加载。均被否。

**一致性**：以 `session_memory` 为权威，`index.json` 可从 `session_memory` 重建；`index.json` 写失败不阻塞查询（标记待修复）。

### D7：`turnsBySession` 内存缓存
**选择**：前端 store 维护 `turnsBySession: Record<sessionId, Turn[]>`。切换离开会话时存当前 `turns`；切回时缓存命中优先用缓存（完整行），未命中走 `event_cache` 重放。

**理由**：同次浏览器会话内切回，内存有完整行（不止 20 行）+ 完整 Turn；仅刷新/重开才降级走重放。覆盖"切来切去"的高频场景。

### D8：老会话摘要回落兼容
**选择**：`getSessionHistory` 返回时，有 `event_cache` 事件流的会话返回事件流（前端重放）；无事件流的存量会话回落 `session_memory` 摘要（前端 `setHistoryTurns` 兼容两种数据源，摘要走简化重建）。

**理由**：不迁移老会话数据，接受"老会话残缺、新会话完整"。前端兼容两源保证平滑过渡。

### D9：`history_cache` 零影响（已验证）
**证据**：① `history_cache._format_history`（`history_cache.py:115-117`）只 `.get("user_query")`/`.get("final_sql")`；② `JsonConversationStore.upsert_turn`（`session_recall.py:249-259`）写 `turns` key 时只硬挑 6 个字段，不带展示字段；③ 展示数据迁出 `session_memory` 后，复用层更精简。两条读取路径均零影响。

## Risks / Trade-offs

- **[resume 跨阶段缓冲复杂]** → 实现前确认 resume graph 机制；若 graph 不支持重取 query 阶段事件，退化方案：resume 完成时从 accumulated state 补构造 query 阶段缺失事件（牺牲部分 stage/llm_thinking，保留 keywords/候选/决策等可从 state 恢复的）。
- **[index 双写一致性]** → 以 `session_memory` 为权威，`index.json` 可重建；写失败不阻塞查询，标记待修复，下次 `list_sessions` 触发自愈。
- **[事件流存储膨胀]** → `result` 截断 20 行；`llm_thinking` 文本可能较大（接受，用户要可恢复）；可配置 TTL 清理老 shard（非本期）。
- **[列表大致降序非严格]** → `created_at` 分片代价，可接受；若需严格 `updated_at` 排序，`index.json` 内跨 shard 排序（增加排序成本，非本期）。
- **[老会话残缺]** → 明确回落摘要，UI 可标"历史会话（部分信息）"。
- **[`event_cache` 文件并发写]** → 复用现有 `Storage` 原子写（tmp→fsync→replace）+ 文件锁。
- **[事件捕获遗漏]** → 必须统一捕获 graph 事件（`query.py:284` 转发）与自构造事件（result/error/done），漏捕会导致重放缺节点。

## Migration Plan

- **部署**：新增 `event_cache` 代码与目录，`session_memory` 零改动。新会话查询完成自动写 `event_cache`；老会话无事件流走回落。无需数据迁移。
- **回滚**：移除 `event_cache` 读写逻辑，前端 `setHistoryTurns` 回退简化重建。`session_memory` 未变，无数据损失。`event_cache/` 目录可删。
- **灰度**：新老会话并存期，前端按"有无事件流"自动选择重放或摘要回落，无切换动作。

## Open Questions

1. **resume graph 机制**：是否从 checkpoint 恢复完整 state？`query` 阶段（`__interrupted__`）的 `captured_events` 能否在 resume 时获取/合并？需读 `src/graph/` resume 路径与 `query.py` resume 处理段确认 D5 具体实现与退化策略。
2. **`index.json` 双写失败处理**：`index.json` 写失败时是否阻塞查询、还是异步重试？（倾向不阻塞）
3. **`llm_thinking` 文本存储开销**：大思考链（qwen3 可能数千字）是否需要单独截断/压缩？（倾向不截断，保完整可恢复）
4. **`event_cache` TTL/清理**：长期累积的老 shard 是否需要清理策略？（本期不做，留后续）
5. **`list_sessions` 分页参数兼容**：现有 `GET /sessions?user_id=` 无分页参数，新增 `page`/`size` 默认返回第一页是否影响现有调用方？（前端配套改，应无外部调用方）

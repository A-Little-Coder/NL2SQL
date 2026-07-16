## Why

当用户已有 ≥20 个会话时，点击「新会话」会导致侧栏列表瞬间坍缩为仅剩 1 个（新创建的会话），之前显示的 20+ 个会话全部消失。触发条件是：最新 shard 恰好装满 20 个 → 创建第 21 个会话时开出新 shard → `list_sessions_paged(page=0)` 返回的是**全新 shard 中的仅 1 个会话**，而不是用户期望的"最近的 20 个会话"。

这个 bug 在之前的 `session-restore-event-cache` change 中没有被发现，因为当时只测试了 shard 未满（≤19）的场景。当 shard 容量边界被触发时，page 语义从"最近的 N 个"变成了"最新 shard 的全部"，造成体验突变。

影响范围：首次触发需要 ≥20 个会话（对开发/测试环境较少见），但一旦积累到 20+ 就会持续复现。

## What Changes

- **`list_sessions_paged` page 语义修正**：page 从"shard 索引"改为"全局偏移"。即 `page=0` 始终返回 index 中按 `created_at` 排序的前 20 个会话，不再受新 shard 创建的影响。
- **后端改动唯一文件**：`src/memory/event_cache.py` 中的 `list_sessions_paged()` 方法（约 25 行代码变更）。
- **前端无感知**：前端 `SessionSidebar` 只知道 `page` 和每页 20 个，不知道后端 shard 分片细节，调用方式不变。
- **惰性加载功能保留**：下拉滚动触发 `loadMore()` → `listSessions(userId, page+1)` 仍正常工作，只是每次返回的内容是全局偏移的连续区间，而非某个 shard 的子集。

## Capabilities

### Modified Capabilities

- `session-display-restore`: "会话列表分页惰性加载" requirement 中，分页语义从"按 shard 分片取整页"修正为"全局偏移滑动窗口"。SHARD_SIZE 写入规则不变（每 shard ≤20，满则开新 shard），仅分页读取逻辑变更。

## Impact

- **后端改动**：`src/memory/event_cache.py:list_sessions_paged()` — 核心修复。
- **前端无改动**：`SessionSidebar` 调用 `listSessions(userId, page)` 的方式完全兼容新的全局偏移语义。
- **测试改动**：`tests/memory/test_event_cache.py` 中 `test_list_sessions_paged` 用例需更新预期结果；新增测试用例覆盖"新 shard 创建后 page=0 仍返回正确内容"。
- **Spec 更新**：`openspec/specs/session-display-restore/spec.md` 中分页要求描述需对齐新的 page 语义。
- **零风险**：不涉及 SSE、event_cache 写路径、resume 缓冲等任何其他模块。

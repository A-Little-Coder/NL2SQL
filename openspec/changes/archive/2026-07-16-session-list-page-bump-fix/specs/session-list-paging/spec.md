## ADDED Requirements

（无新增要求。）

## MODIFIED Requirements

### Requirement: 会话列表分页惰性加载（page 语义修正）

系统 SHALL 提供按 `created_at` **全局排序**后滑动窗口分页的会话列表接口，每页最多 20 个会话，按 `created_at` 倒序。**`page=0` 始终返回 index 中时间最近的 ≤N 个会话（N≤20），不因新 shard 创建而跳变。** 前端左栏 SHALL 初始只加载最新一页，向下滚动到底时惰性加载更早一页。

此修正不改变 shard 写入规则：每个 shard 目录仍 ≤20 会话、满则开新 shard；仅改变分页**读取**逻辑——从"按完整 shard 取整页"改为"全局排序后取连续区间"。

#### Scenario: 点「新会话」后 page=0 内容稳定
- **WHEN** 用户有 20 个会话（全在 shard_0001）并已展示在侧栏
- **AND** 用户点击「新会话」（触发 register_session → 新开 shard_0002）
- **THEN** `list_sessions_paged(page=0, size=20)` 仍返回 index 中 `created_at` 最早的 20 个会话中的最新者（即原 20 个会话中最新的 15 个 + 新会话等，总计 ≤20 个）
- **AND** 前端不会看到列表突变为仅剩 1 个会话

#### Scenario: 下拉加载更早页正常工作
- **WHEN** 用户在左栏滚动到底部
- **THEN** 调用 `listSessions(userId, 1)` 获取再往前 20 个会话并追加到列表
- **AND** 前后两页之间没有遗漏或重复

#### Scenario: 超过 40 个会话时持续分页
- **WHEN** 用户有 60 个会话
- **THEN** page=0 返回最新的 20 个，page=1 返回第 21~40 个，page=2 返回第 41~60 个
- **AND** has_more 正确反映是否还有更多数据

## 实现任务

- [x] task 1: 修改 `src/memory/event_cache.py` 中 `list_sessions_paged()` 方法，将 page 语义从"shard 索引"改为"全局偏移滑动窗口"
- [x] task 2: 更新 `tests/memory/test_event_cache.py` 中的现有分页测试用例（调整预期结果），并新增覆盖 bug 场景的测试
- [x] task 3: 更新 `openspec/specs/session-display-restore/spec.md` 中的"会话列表分页惰性加载"描述，对齐新的 page 语义
- [x] task 4: 手动验证 E2E：积累 ≥20 个会话 → 点「新会话」→ 确认侧栏仍显示 ≤20 个合理内容 → 下拉加载更多 → 确认能加载更早会话

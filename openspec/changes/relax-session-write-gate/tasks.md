## 1. 核心代码：放松写入闸门 + 新增标记

- [x] 1.1 简化 `_should_write_session_turn`（`src/api/routes/query.py:55-60`）：仅保留 `__interrupted__` 拦截，其余全放行
- [x] 1.2 在 `turn_data` 构造中（`query.py:289-308`）新增 `reuse_eligible` 计算：`bool(final_sql) and not fix_failed and not rejection_reason`
- [x] 1.3 `_ALLOWED_TURN_FIELDS` 白名单（`src/memory/session_memory.py:67`）新增 `reuse_eligible`

## 2. 核心代码：history_cache fallback 过滤

- [x] 2.1 `main_graph.py:168` 修改：从 `recalled_history or conversation_history` 改为 `recalled_history or eligible_turns`，其中 `eligible_turns` 过滤掉 `reuse_eligible=False` 的轮次，旧数据（缺字段）按 `bool(t.get("final_sql"))` 推导
- [x] 2.2 验证两处 `conversation_history` 消费方（改写模块 + task_planner follow-up）不做额外过滤，全部轮次可见（指代消解和 follow-up 理解需要）

## 3. 现有测试更新

- [x] 3.1 `tests/api/test_session_write_semantics.py`：反转断言——"SmartFix 失败不入会话"改为"SmartFix 失败写入 + reuse_eligible=False"
- [x] 3.2 `tests/api/test_session_write_semantics.py`：新增"TaskPlanner 拒答写入 + reuse_eligible=False"、"fail-fast 早退写入 + reuse_eligible=False"断言
- [x] 3.3 `tests/api/test_session_write_semantics.py`：确认"反问挂起"断言不变（仍不写）

## 4. 新增测试

- [x] 4.1 `tests/graph/test_history_cache_node.py`：新增 history_cache fallback 过滤单测——注入含 `reuse_eligible=False` 报错 SQL 的 `conversation_history`，断言不被复用
- [x] 4.2 `tests/graph/test_history_cache_node.py`：新增旧数据兼容单测——缺 `reuse_eligible` 字段时按 `bool(final_sql)` 推导，旧文件行为不变

## 5. 端到端测试（Playwright）

- [x] 5.1 编写 Playwright e2e 测试：第 1 轮制造 SmartFix 全失败，第 2 轮 follow-up 输入"换个条件再试试"，验证改写模块能解析指代、history_cache 不复用报错 SQL
- [x] 5.2 编写 Playwright e2e 测试：第 1 轮制造 TaskPlanner 拒答，第 2 轮 follow-up 输入"那个改成华东"，验证改写模块能解析指代

## 6. 文档

- [x] 6.1 更新 `openspec/specs/session-memory-write-semantics/spec.md` 主文件：将时间线 delta spec 合并到主 spec（写入条件反转 + `reuse_eligible` 标记 + 消费方读时过滤）
## Why

改写模块（Rewrite 子图）做指代消解时，需要看到上一轮的 `user_query` 才能解析"那个"、"换个条件"等省略指代。但当前 `_should_write_session_turn` 闸门拦截了 SmartFix 全失败、TaskPlanner 拒答、fail-fast 早退三种轮次，导致这些轮次不进 SessionMemory，改写模块看不到它们，指代消解在 follow-up 中失效。

## What Changes

1. **放松 `_should_write_session_turn` 闸门**：仅保留反问挂起（`__interrupted__`）拦截，其余全写入 SessionMemory。Rewrite 拒答特例吸收进统一逻辑。
2. **新增 `reuse_eligible` 标记**：每轮写入时标记"这轮 SQL 是否可被 history_cache 复用"。SmartFix 全失败 / TaskPlanner 拒答 / fail-fast 早退 / Rewrite 前置拒答 → `False`，成功轮次 → `True`。
3. **SessionMemory 白名单新增 `reuse_eligible`**：`_ALLOWED_TURN_FIELDS` 加字段。
4. **history_cache fallback 路径增加过滤**：`check_history = recalled_history or eligible_turns`，其中 `eligible_turns` 过滤掉 `reuse_eligible=False` 的轮次，避免报错 SQL 被 LLM 当作复用候选。
5. **recall 库 `_is_successful_for_session_recall` 闸门不动**：改写模块不读 recall 库，保持其"仅存可复用 SQL"的纯净。
6. **反转 `session-memory-write-semantics` spec**：从"失败轮次不写入"改为"全写入 + 读时按 `reuse_eligible` 过滤"。

**BREAKING**: 现有测试 `test_session_write_semantics.py` 中"SmartFix 失败不入会话"的断言会反转，需要更新。

## Capabilities

### New Capabilities
- （无——本变更不引入新能力，而是修改现有会话写入语义）

### Modified Capabilities
- `session-memory-write-semantics`: 写入条件从"仅产出可执行 SQL 的成功轮次"改为"所有轮次（除反问挂起外）均写入，写入时附带 `reuse_eligible` 标记，消费者按需读时过滤"

## Impact

| 模块 | 影响 |
|------|------|
| `src/api/routes/query.py` | `_should_write_session_turn` 逻辑简化（仅保留 `__interrupted__` 拦截）；`turn_data` 构造新增 `reuse_eligible` 计算 |
| `src/memory/session_memory.py` | `_ALLOWED_TURN_FIELDS` 白名单新增 `reuse_eligible` |
| `src/graph/main_graph.py` | history_cache 节点 fallback 过滤路径（line 168），新增 `reuse_eligible` 筛选 |
| `src/memory/session_recall.py` | 无变更（recall 库闸门不动） |
| `src/memory/memory_updater.py` | 无变更（recall 库写入不动） |
| `openspec/specs/session-memory-write-semantics/spec.md` | 写入语义反转，需重写 |
| `tests/api/test_session_write_semantics.py` | 现有断言需更新（失败轮次从"不写"改为"写入+标记"） |
| `tests/graph/test_history_cache_node.py` | 新增 fallback 过滤单测 |
| 前端 / API 契约 | 无影响（`reuse_eligible` 是内部字段，不在 SSE 事件或 API 响应中暴露） |
## Context

当前 SessionMemory 写入由 `_should_write_session_turn`（`src/api/routes/query.py:55-60`）拦截，仅成功轮次（`bool(final_sql) and not fix_failed`）和 Rewrite 前置拒答（特例）写入。改写模块只读 SessionMemory 的 `conversation_history`，拦截导致失败轮次对改写不可见，指代消解在 follow-up 中失效。

history_cache 节点有两条复用候选来源：主路径（recall 库，Chroma+JSON，只存成功轮次）和 fallback 路径（`conversation_history`，当 recall 为空时）。两条路径写入闸门独立，本变更只动 SessionMemory 这条线。

## Goals / Non-Goals

**Goals:**
- 失败轮次（SmartFix 全失败、TaskPlanner 拒答、fail-fast 早退、Rewrite 前置拒答）写入 SessionMemory，`user_query` 对改写模块可见
- 新增 `reuse_eligible` 标记标识每轮 SQL 是否可安全复用，history_cache fallback 按标记过滤，不将报错 SQL 喂给 LLM
- 旧数据兼容：缺 `reuse_eligible` 字段时按 `bool(final_sql)` 推导

**Non-Goals:**
- 不改 recall 库写入闸门（`_is_successful_for_session_recall`），recall 库保持"仅存可复用 SQL"
- 不改 `__interrupted__` 反问挂起拦截（反问挂起等 resume 完成后再写完整轮次）
- 不改前端 / API 契约（`reuse_eligible` 不暴露给前端）
- 不改改写模块的读取逻辑（`_format_history_lines` 已有 `[被拒答: ...]` 渲染，兼容新增写入）

## Decisions

### D1: 字段名 `reuse_eligible` 而非 `success`

`success` 语义模糊——拒答轮次谈不上"成功"但关键是不可复用。`reuse_eligible` 精确表达"这轮 SQL 能否被 history_cache 复用"。recall 库那边继续用它的 `success`，两套命名，语义不同。

### D2: 仅 SessionMemory 动，recall 库不动

改写模块只读 SessionMemory 的 `conversation_history`，不读 recall 库。放松 recall 库写入闸门无收益且浪费索引空间（失败 SQL 写进 Chroma 也是被 `success=True` 过滤掉）。两级存储各有"正确"的闸门，独立调整。

### D3: 旧数据用 `bool(final_sql)` 推导 `reuse_eligible`

旧闸门下写入的都是成功轮次（`final_sql` 非空即可复用），所以推导过去安全。避免迁移脚本。

### D4: `reuse_eligible` 计算位置在 API 路由层（query.py）

`turn_data` 构造时计算，而非在 SessionMemory.add_turn 内部计算。因为 `reuse_eligible` 需要 `accumulated state` 的全局信息（`final_sql`、`fix_failed`、`rejection_reason`），不在 SessionMemory 的职责范围内。

### D5: 旧数据推导表达式

```python
t.get("reuse_eligible", bool(t.get("final_sql")))
```

旧闸门下 `final_sql` 非空即成功轮次，推导安全。新数据 `reuse_eligible=False` 的轮次（SmartFix 全失败等）有 `final_sql` 但 `reuse_eligible` 显式 False，推导不会覆盖。

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| SmartFix 全失败的报错 SQL 写进 SessionMemory，history_cache fallback 复用 → 再跑一遍报错。 | `reuse_eligible=False` 标记 + history_cache fallback 过滤，双重防护。 |
| 旧数据推导不严谨：缺 `reuse_eligible` 字段的轮次按 `bool(final_sql)` 推导，如果旧数据存在 `final_sql` 非空但不可复用的轮次，推导会误判。 | 旧闸门下验证：`final_sql` 非空时 `fix_failed` 必为 False（闸门条件 `not fix_failed`），所以推导安全。 |
| 改写模块看到失败轮次后，如果 `_format_history_lines` 渲染了报错相关字段，LLM 可能误解。 | 改写模块只读 `user_query`，不读 `final_sql`/`error`，渲染逻辑不变。 |
| 写入量增加：失败轮次也占 SessionMemory 文件空间。预计每用户每会话多 1-3 轮，数据量可忽略。 | 无缓解必要。 |
| 反转 spec 后，消费方（task_planner follow-up）原本只见过成功轮次，现在会看到失败轮次。 | task_planner 的 `conversation_history` 输入增加失败轮次，其 follow-up 理解可能获益（"那个报错的查询"），但需确认不退化。表中列为"待确认"。 |
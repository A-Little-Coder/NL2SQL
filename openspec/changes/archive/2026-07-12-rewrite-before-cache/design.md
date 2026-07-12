## Context

当前流水线从 START 直接进入 `history_cache`，然后到 `task_planner`。指代消解能力分散在 IR 的 `KEYWORD_EXTRACTION_WITH_HISTORY_PROMPT` 和 TaskPlanner 的隐式逻辑中，没有独立的、可观测的改写环节。导致：

1. 改写发生在 cache 检测之后，改写后的完整 query 无法提升缓存命中率
2. 改写逻辑分散在多个 prompt 中，难以一致维护
3. 拒答不产生会话历史，用户无法在下一轮继续
4. TaskPlanner 同时承担拒答、反问、意图拆解，职责过重

v2 重新设计：前置拒答检测独立节点 + Rewrite 子图（含改写+反问）+ TaskDecomposer 精简为纯意图拆解。

## Goals / Non-Goals

**Goals:**
- 前置拒答检测节点（PreReject）：硬性写操作/空查询检测，不调 LLM
- Rewrite 子图：两个子节点（问题检测 + 改写执行）通过条件边循环协作
- 改写最多 2 次后仍有问题 → 触发反问澄清（interrupt），用户补充信息后继续改写
- 反问可多次，直到检测通过
- TaskDecomposer 删除所有拒答/反问功能，只保留意图拆解
- IR 删除隐式消歧 prompt 和历史参数

**Non-Goals:**
- 不改变 IR/SS/CG/Execution/Decision 子图内部逻辑
- 不改变 history_cache 的命中检测算法（只改变输入 query）
- 不改变 answerability_check 的逻辑
- 不改变 SSE 事件流整体结构（只增加 rewrite/clarification 相关事件）

## Decisions

### D1: 前置拒答检测为独立节点
- **方案**: `START → pre_reject → rewrite_subgraph → history_cache → ...`
- **理由**: 写操作检测是安全层的硬规则，不依赖 LLM，应在所有 LLM 调用之前拦截。独立节点使职责清晰，且 Rewrite 子图只处理语义改写。
- **替代方案**: 合并在 Rewrite 节点内 → 违反单一职责，且改写 LLM 调用前需要先做硬性检查，逻辑交错。

### D2: Rewrite 子图用条件边循环，而非节点内 while
- **方案**: 两个子节点（DetectIssues / RewriteExecute）通过图级别条件边路由实现循环
- **理由**: 需要支持反问澄清（interrupt），而 interrupt 只能在图级别节点内正常工作。节点内 while 循环无法在中断后恢复。
- **替代方案**: 节点内 while 循环 → 无法实现反问 interrupt，改写失败只能拒答，用户无法补充信息。

### D3: 改写循环 + 反问澄清双循环
- **方案**:
  - 改写循环：DetectIssues → RewriteExecute → DetectIssues（最多 2 次，条件边路由）
  - 反问循环：Clarify（interrupt）→ RewriteExecute → DetectIssues（可多次）
- **理由**: 改写循环处理指代/歧义/缺失等上下文可解决的问题；反问循环处理上下文不足以解决的问题，用户补充信息后继续改写。
- **替代方案**: 统一为 2 次改写 + 直接拒答 → 用户无法补充信息，体验差。

### D4: 改写后覆盖 `user_query`
- **方案**: Rewrite 子图将 `user_query` 改写为完整语义 query，同时设置 `rewritten_query` 字段记录改写值
- **理由**: 下游所有节点（history_cache / task_decomposer / ir / cg）都从 `user_query` 读取，无需逐个修改。
- **替代方案**: 新增 `final_query` 字段 → 需修改所有下游节点读取位置，侵入性大。

### D5: 改写拒答/反问写入会话历史
- **方案**: `_should_write_session_turn` 放宽条件，`rewrite_rejection_reason` 非空时也写入
- **理由**: 用户可在下一轮查询中利用上下文补充信息。反问挂起时不写入（等 resume 完成后再写）。
- **风险**: 拒答轮次无 `final_sql`，友好提示即可。

### D6: TaskDecomposer 删除所有反问/拒答逻辑
- **方案**: 到 TaskDecomposer 的 query 已是 Rewrite 改写后的完整语义 query，无需再做拒答/反问。只保留意图拆解（单意图/多意图）。
- **理由**: 职责单一化。Rewrite 子图已经处理了所有改写/拒答/反问的需求。
- **替代方案**: 保留 CLARIFY → 与 Rewrite 子图的反问功能重叠，职责边界不清晰。

## Risks / Trade-offs

| 风险 | 缓解措施 |
|------|----------|
| [R1] 改写引入错误语义：LLM 改写可能改变用户原意 | 改写 prompt 中强调"保持原意，只补全缺失信息"；`rewritten_query` 透传前端供用户确认 |
| [R2] 改写循环增加 1-2 次 LLM 调用，延迟增加 | 改写仅发生在有指代/歧义/缺失时；无问题 query 一次 LLM 调用即可 PASS（约 1 次额外调用） |
| [R3] 改写后 query 与原意偏差导致 cache 误匹配 | history_cache 的 confidence 阈值（0.8）仍生效，低置信度不会命中 |
| [R4] 反问循环可能无限进行 | 设 max_clarify_rounds 硬上限（如 5 次），达上限后降级执行 |
| [R5] 子图嵌套增加主图复杂性 | 与 single_query_graph 相同的嵌套模式，LangGraph 原生支持子图调用 |

## Migration Plan

1. 新建 `src/rewrite/` 模块（rewrite_graph.py, prompts.py）
2. 修改 `src/graph/state.py` 新增字段
3. 重构 `src/graph/main_graph.py` 为 pre_reject → rewrite 子图 → history_cache 结构
4. 改名 `src/clarification/task_planner.py` → `task_decomposer.py` 并删除拒答/反问逻辑
5. 修改 `src/clarification/prompts.py` 删除 REJECT 和 CLARIFY 规则
6. 修改 `src/clarification/dialog.py` — 反问逻辑迁移到 Rewrite 子图
7. 修改 `src/retrieval/prompts.py` 删除 WITH_HISTORY 版本
8. 修改 `src/retrieval/ir_graph.py` 和 `information_retrieval.py`
9. 修改 `src/api/routes/query.py` 放宽写会话条件
10. 更新所有测试用例
11. 运行测试验证

## Open Questions

- 无（v2 设计已确认）
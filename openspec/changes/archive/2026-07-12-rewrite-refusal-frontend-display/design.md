## Context

rewrite-v2 把 `pre_reject` 与 Rewrite 子图（`detect_issues` ⇄ `rewrite_execute` ⇄ `clarify`）插到流水线最前面，但这些环节对用户隐形：

```
后端 emit                       前端 SseEvent 联合   reducer case   时间轴可见
─────────────────────────────── ─────────────────── ────────────── ──────────────
stage(pre_reject, started/done) ✓ StageEvent        ✓ stage        ✗ mapStageNode=null 丢弃
stage(rewrite,  started/done)   ✓ StageEvent        ✓ stage        ✗ 同上丢弃
rewrite (rewrite_execute 发)    ✗ 未定义            ✗ default 丢弃 ✗ 完全不可见
rewrite_detect (detect_issues)  ✗ 后端根本没 emit   -              ✗ 完全不可见
value_rewrite                   ✗ 未定义            ✗ default 丢弃 ✗ 不可见
cache_confirm                   ✗ 未定义            ✗ default 丢弃 ✗ 不可见
clarify -> clarification        ✓ Clarification     ✓ clarification ✓ 反问气泡可见
```

拒答时 `pre_reject` 设 `rejection_reason`，`_wrap_node` 的 `stage done` 透传它，reducer 把它 upsert 成通用 `error` 节点（`reducer.ts:113-119`），与 answerability/decision 拒答混在一起。本 change 把这些动作可见化，定位为**可观测**（让用户知道发生了什么），非**可控**（不让用户干预改写）。

## Goals / Non-Goals

**Goals:**
- 5 个动作在前台可见：前置拒答、改写检测、改写执行、值参数改写、复用确认
- 改写检测/执行的多轮迭代在时间轴上能看清（每轮独立节点，按 detect→rewrite→detect 交替呈现）
- 前置拒答与通用 error 视觉区分
- 后端零改动优先：能靠现有事件解决的绝不加后端事件
- 前置拒答覆盖语义级危险指令（换皮写法、敏感数据探查），不再只靠字面关键词（D9，需后端 LLM）
- schema 选择全空时给用户明确拒答反馈，不再静默中断（D10，需后端 emit）

**Non-Goals:**
- 不加"改写结果用户确认"interrupt（design.md R1 的"供用户确认"延后，本期只透传展示）
- 不改 rewrite 子图内部循环逻辑 / 路由 / MAX_ROUNDS
- 不改 history_cache / value_rewrite / cache_confirm 节点业务逻辑
- 不改 SSE 传输基础设施（StreamEmitter / 心跳）

## Decisions

### D1: 可观测优先，不加改写确认 interrupt
- **方案**：只把改写结果/原因展示出来，不中断流程让用户确认改写
- **理由**：可控会改变流程时序（多一次 interrupt+resume），且改写 prompt 已强调保持原意；先让动作透明，确认机制视用户反馈再加
- **替代**：加 interrupt 让用户确认改写 → 改动大、增延迟，且改写多数场景正确，逐次确认体验差

### D2: 多轮节点用 `TimelineNode.id`，而非新增多个 type
- **方案**：`TimelineNode` 加可选 `id` 字段（如 `rewrite_r1`/`detect_r2`）；`upsert` 改为**有 id 按 id 匹配、无 id 回退按 type**；`selectedNode` 仍是 type 级别，Inspector 展示该 type 全部轮次
- **理由**：`detect_issues`/`rewrite_execute` 各最多 2~N 轮（含反问后再改写），用 `rewrite_1`/`rewrite_2` 等 type 会污染 `TimelineNodeType` 字面量联合且不动态；id 方案对单次节点（cache/ir/cg）零影响
- **替代**：同 type 合并为一个节点、summary 表达轮次演进 → 看不清迭代，已被用户否决

### D3: `pre_reject` 纯前端，不加后端专用事件
- **方案**：`mapStageNode("pre_reject")` 映射到新 type `pre_reject`；`stage` started/done 点亮该节点；`stage done` 携带 `rejection_reason` 时把 `pre_reject` 节点置 error 态（红），**不**再 upsert 通用 `error`；通过时 summary="通过"
- **理由**：`rejection_reason` 文本已是友好说明（"本服务仅支持查询…"），通过时"通过"二字足够；后端零改动最干净
- **替代**：后端 `pre_reject` emit 专用事件带结构化 rule 字段 → 信息增益小，不值当

### D4: `detect_issues` 新增 `rewrite_detect` 事件（唯一后端改动）
- **方案**：`make_detect_issues_node` 在返回前 `emit_safe("rewrite_detect", {round, has_issues, issue_detail, issue_types})`，`round = state.rewrite_round + 1`（第几次检测）
- **理由**：检测到的问题是改写的原因，没有这个事件则"为什么改写"无处可看；轮次编号与 `rewrite_execute` 的 `rewrite_round` 对齐（detect r1 → rewrite r1 → detect r2 → rewrite r2）
- **边界**：LLM 降级（无 LLM 视为无问题）时仍 emit（`has_issues=false`），保证时间轴节点存在

### D5: `cache_confirm` 独立节点，不合并进 cache
- **方案**：`cache_confirm` 事件点亮独立 `cache_confirm` 节点（"确认复用 ✓/✗"），与 `cache`（命中检测）节点分开
- **理由**：cache 命中检测、反问（clarification 气泡）、用户确认是三个动作；反问已走 clarification 事件点 `clarify` 节点，确认结果单独成节点使"用户做了什么选择"可见
- **替代**：把 approved 信息追加到 cache 节点 summary → 混淆"检测"与"确认"两个动作

### D6: `value_rewrite` 单次节点
- **方案**：`value_rewrite` 事件点亮 `value_rewrite` 节点，单次（不循环），summary 表达 changed/未变更
- **理由**：value_rewrite 节点只在 cache 命中后执行一次，无多轮问题

### D7: Inspector 多轮详情按数组呈现
- **方案**：`details.rewrite = { rounds: [{round, originalQuery, rewrittenQuery, reason}] }`、`details.rewriteDetect = { rounds: [{round, hasIssues, issueDetail, issueTypes}] }`；Inspector 渲染轮次列表，每轮一个区块
- **理由**：`selectedNode` 是 type 级别无法精确 pin 到轮，列表呈现让用户在一次检视中看完整个迭代过程

### D8: rewrite_execute 必须写回 user_query（联调发现的 rewrite-v2 bug 修复）
- **方案**：`rewrite_execute` 的 3 个 return（正常/无LLM透传/异常降级）都写回 `user_query`：正常路径 `"user_query": rewritten_query`，透传/降级 `"user_query": user_query`
- **理由**：rewrite-v2 原 `rewrite_execute` 只设 `rewritten_query` 不更新 `user_query`，LangGraph 浅合并导致 `state.user_query` 全程是原句，下一轮 `detect_issues` 读原句（仍含指代词）又触发改写，每轮基于原句得到相同结果--改写实际未生效，2 轮后强制转反问（死循环假象）。写回后下一轮 detect 读改写后 query，无指代则通过，1 轮收敛
- **边界**：`original_query` 在子图初始化时锁定原句、`rewrite_execute` 不动它，前端"原句"展示仍正确；透传/降级写回原值不影响 state

### D9: pre_reject 升级为纯 LLM 语义判定（保留空查询规则快路径）
- **方案**：`pre_reject` 节点保留"空查询/纯空白"规则快路径（零成本、确定性）；其余查询调 LLM 判定属于 `增删改意图(write_op)` / `危险信息指令(dangerous_info)` / `正常(normal)`，输出结构化 JSON `{reject, category, reason}`，`thinking=False`、`run_name="pre-reject"`。`reject=true` 设 `rejection_reason`(reason 文本) + `rewrite_rejection_reason` + `pre_reject_category`；通过也写 `pre_reject_category="normal"`。stage 事件透传 `category` 供前端展示
- **理由**：纯字面关键词漏判换皮写法（"把学生表导出来"）与危险信息指令（刺探 `information_schema`、批量导出敏感字段），且误伤含"更新"字段的正常查询；LLM 语义判定覆盖面与准确度更高。空查询保留规则是因为它无需语义、且 LLM 对空输入无意义
- **降级**：LLM 不可用 / 异常 / 解析失败 -> 放行（`category="normal"`，不阻断流水线），宁放行不误杀；写操作漏拦风险存在，后续可回补规则关键词兜底
- **替代**：双层（规则快路径 + LLM）-> 用户选纯 LLM 主导，规则仅留空查询；明确 SQL 写关键词不再单独拦截，交 LLM 统一判
- **边界**：`pre_reject_category` 写入 state，`_wrap_node` 的 stage done 透传该字段；前端 `details.preReject.category` 展示中文化标签（写操作/危险信息/通过）

### D10: schema 选择全空时显式拒答（不再静默 END）
- **方案**：`make_schema_finalize_node` 检测 `selected_schema` 为空时，设 `rejection_reason="未在数据库中找到与查询相关的表或字段，请尝试换一种表述或确认数据范围"` + emit `schema_empty` 事件（带 reason），route 仍 END。前端消费 `schema_empty` 渲染 error 态节点
- **理由**：原 `route_after_schema_finalize` 判空直接 END，ss/schema_finalize 均未 emit 业务事件、未设 rejection_reason，前端看到流水线在 SS 后戛然而止无结果无解释（"没输出"）。显式拒答让用户知道"为什么没有结果"并给出可操作建议
- **边界**：`schema_empty` 是新 SSE 事件类型；与 `answerability` 拒答语义不同（answerability=有 schema 但不可回答，schema_empty=连表都没选出来），独立节点呈现；route 仍走 END，不进 cg
- **替代**：复用 `answerability` 事件 answerable=false -> 语义混淆（schema 空时根本没到 answerability 节点）；复用通用 `error` -> 与异常混同，看不出是"无匹配表"

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| [R1] `upsert` 改造为 id 优先可能影响单次节点合并 | 单次节点不传 id，回退按 type 匹配，行为不变；reducer 单测覆盖 cache/ir/cg 合并仍正确 |
| [R2] `pre_reject` 拒答路径 stage done 的 rejection_reason 与后续可能的 error 事件双显 | reducer 在 `node==="pre_reject"` 时不 upsert 通用 error；pre_reject 节点本身承载 error 态 |
| [R3] `detect_issues` emit 在 LLM 异常降级时漏发导致时间轴缺检测节点 | 降级路径也 emit（has_issues=false），保证节点存在；emit 包在 try 内不影响主流程 |
| [R4] 多轮改写节点过多撑长时间轴 | 实际 MAX_REWRITE_ROUNDS=2，加反问后 rewrite_execute 最多约 2+clarify_rounds 次，可接受；节点 summary 简洁 |
| [R5] `rewrite` 事件改写后 query 与用户原意偏差被放大展示 | 正是可观测的价值：用户能看到改写并发现偏差；不做确认干预（D1） |
| [R6] pre_reject 纯 LLM 判定，LLM 抽风放行写操作 | 降级策略为"放行不误杀"，写操作漏拦风险存在；prompt 强约束 + few-shot，必要时可回补规则关键词兜底 |
| [R7] LLM 判定增加每次查询延迟（pre_reject 多一次 LLM 调用） | `thinking=False` 结构化输出延迟可控；空查询走规则快路径不调 LLM |

## Migration Plan

1. 后端：`rewrite_subgraph.py` `detect_issues` 加 `emit_safe("rewrite_detect", …)`
2. 后端测试：补 emit 断言
3. 前端 `api/types.ts`：新增 4 个事件接口 + 扩展 `SseEvent`
4. 前端 `store/types.ts`：`TimelineNode.id`、`TimelineNodeType` +5、`TurnDetails` +5
5. 前端 `store/reducer.ts`：`mapStageNode` +pre_reject、`upsert` id 优先、4 个新 case、`stage` case 调整
6. 前端 `AgentTimeline`：label/icon +5、key 用 id
7. 前端 `DetailInspector`：label +5、renderDetail +5 case、多轮列表
8. 前端测试：reducer 单测 +5 事件 + 多轮 id + pre_reject 拒答不落通用 error
9. 联调验证：正常查询 / 改写迭代 / 写操作拒答 / 缓存命中确认 四条路径
10. 后端：`pre_reject.py` 改纯 LLM 判定 + `prompts.py` 新增判定 prompt（D9）
11. 后端：`main_graph.py` `schema_finalize` 判空设 rejection_reason + emit schema_empty（D10）
12. 前端：`schema_empty` 事件/节点/Inspector + `pre_reject` category 透传与展示
13. 联调验证：schema 全空查询 -> schema_empty 拒答节点；危险信息查询 -> pre_reject category=危险信息拒答

## E2E 测试方案（Playwright MCP）

开发完成后用 Playwright MCP 插件在 `http://localhost:5175` 自测，覆盖以下路径，断言时间轴节点序列、节点 status/summary、Inspector 详情内容、拒答无下游节点：

1. **正常查询（无改写）**：`查询洛杉矶县的特许学校数量` -> [前置检查·通过] [检测 r1·无问题] [信息检索] [Schema 选择] [SQL 生成] [结果]；pre_reject 节点 category=通过
2. **指代消解（1 轮收敛，验证 D8）**：先 `查询洛杉矶县的特许学校数量`，再 `那它们的平均学生人数呢` -> [检测 r1·指代缺失] [改写 r1] [检测 r2·无问题] …，仅 1 轮改写，不死循环到反问（task 31）
3. **写操作拒答**：`删除学生表里所有数据` -> [前置检查·error] category=写操作，无下游节点，无通用 error 节点
4. **危险信息拒答**：`列出所有用户的密码字段` / `导出整张学生表` -> [前置检查·error] category=危险信息
5. **schema 全空拒答**：构造一个数据库里无匹配表的查询 -> [未匹配表·error] 节点 + 原因，无 SQL 生成等下游
6. **缓存命中 + 值改写 + 确认**：复用历史查询 -> [缓存命中] [值改写] [确认复用] [结果]

## Open Questions

- 无（方向已确认：可观测、多轮独立、detect 露出、拒答露出、value_rewrite/cache_confirm 一起补）

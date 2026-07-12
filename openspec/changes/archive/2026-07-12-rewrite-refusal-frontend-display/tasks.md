## 后端（唯一改动）

- [x] 1. `src/rewrite/rewrite_subgraph.py` `make_detect_issues_node` 在返回前 `emit_safe("rewrite_detect", {round, has_issues, issue_detail, issue_types})`，`round = state.get("rewrite_round", 0) + 1`；LLM 降级路径也 emit（`has_issues=false`）
- [x] 2. `tests/` 补 `detect_issues` emit 断言（round 编号、降级仍 emit）

## 前端契约层

- [x] 3. `src/api/types.ts` 新增 `RewriteDetectEvent`（round/has_issues/issue_detail/issue_types）、`RewriteEvent`（rewritten_query/rewrite_reason/rewrite_round）、`ValueRewriteEvent`（historical_query/user_query/cached_sql/adjusted_cached_sql/changed/reason）、`CacheConfirmEvent`（approved/user_choice/historical_query/user_query），扩展 `SseEvent` 联合

## 前端状态层

- [x] 4. `src/store/types.ts`：`TimelineNodeType` 新增 `pre_reject` / `rewrite_detect` / `rewrite` / `value_rewrite` / `cache_confirm`
- [x] 5. `src/store/types.ts`：`TimelineNode` 加可选 `id?: string` 字段
- [x] 6. `src/store/types.ts`：`TurnDetails` 新增 `preReject` / `rewriteDetect`（rounds 数组）/ `rewrite`（rounds 数组）/ `valueRewrite` / `cacheConfirm`

## 前端 reducer

- [x] 7. `src/store/reducer.ts` `mapStageNode` 增加 `pre_reject` -> `pre_reject` 映射
- [x] 8. `src/store/reducer.ts` `upsert` 改造：节点有 `id` 时按 id 匹配，无 id 回退按 type；新节点带 id 时写入 id
- [x] 9. `src/store/reducer.ts` `stage` case 调整：`node==="pre_reject"` 且 `rejection_reason` 非空时，把 `pre_reject` 节点置 error 态 + summary，不再 upsert 通用 `error`；其他节点 rejection_reason 行为不变
- [x] 10. `src/store/reducer.ts` 新增 `rewrite_detect` case：按 round 追加 detect 节点（id=`detect_r{round}`）+ 累积 `details.rewriteDetect.rounds`
- [x] 11. `src/store/reducer.ts` 新增 `rewrite` case：按 round 追加 rewrite 节点（id=`rewrite_r{round}`）+ 累积 `details.rewrite.rounds`（含原句/改写后/原因）
- [x] 12. `src/store/reducer.ts` 新增 `value_rewrite` case：点 `value_rewrite` 节点 + 写 `details.valueRewrite`
- [x] 13. `src/store/reducer.ts` 新增 `cache_confirm` case：点 `cache_confirm` 节点（approved 决定 summary ✓/✗）+ 写 `details.cacheConfirm`

## 前端组件

- [x] 14. `src/components/AgentTimeline/index.tsx` `NODE_LABEL` / `NODE_ICON` 补全 5 类（pre_reject / rewrite_detect / rewrite / value_rewrite / cache_confirm）；Timeline item `key` 用 `node.id ?? node.type`
- [x] 15. `src/components/DetailInspector/index.tsx` `NODE_LABEL` 补全 5 类
- [x] 16. `src/components/DetailInspector/index.tsx` `renderDetail` 新增 5 个 case：pre_reject（通过/拒答原因）、rewrite_detect（轮次列表）、rewrite（轮次列表：原句->改写后/原因）、value_rewrite（historical/cached->adjusted/changed/reason）、cache_confirm（approved/user_choice）
- [x] 17. 缓存命中短路逻辑（`AgentTimeline` cacheHit filter）确认是否保留 `pre_reject` / `rewrite_detect` / `rewrite` 节点（这些在 cache 之前发生，应保留可见）

## 前端测试

- [x] 18. `frontend/tests/` reducer 单测：`rewrite_detect` 事件 -> detect 节点 + details
- [x] 19. reducer 单测：`rewrite` 事件多轮 -> 多个独立 rewrite 节点（id 递增）+ rounds 数组
- [x] 20. reducer 单测：`value_rewrite` / `cache_confirm` 事件 -> 对应节点 + details
- [x] 21. reducer 单测：`stage(pre_reject, done, rejection_reason)` -> `pre_reject` error 节点，**不**生成通用 `error` 节点
- [x] 22. reducer 单测：`stage(pre_reject, done)` 无 rejection_reason -> `pre_reject` done 节点 summary="通过"
- [x] 23. reducer 单测：单次节点（cache/ir/cg）upsert 在 id 改造后仍按 type 合并，无回归
- [x] 24. `npm run test` 全绿

## 联调验证

- [x] 25. 正常查询（无改写）：时间轴 [前置检查✓] [检测 r1✓] [信息检索] … [结果]
- [x] 26. 指代消解（1轮改写）：[前置检查✓] [检测 r1·指代缺失] [改写 r1] [检测 r2✓] …
- [x] 27. 写操作拒答：[前置拒答·写操作] 红色节点，无后续节点，无通用 error 节点
- [x] 28. 缓存命中 + 值改写 + 确认：[前置检查✓] [检测 r1✓] [缓存命中] [值改写] [确认复用✓] [结果]

## 后端 bug 修复（联调发现）

- [x] 29. 修复 `src/rewrite/rewrite_subgraph.py` `rewrite_execute` 3 个 return（正常/无LLM透传/异常降级）写回 `user_query`：正常路径 `"user_query": rewritten_query`，透传/降级 `"user_query": user_query`。避免下一轮 `detect_issues` 读原句导致改写死循环（每轮基于原句得到相同结果，2 轮后强制转反问）
- [x] 30. `tests/graph/test_rewrite.py` 补测试：rewrite_execute 正常路径写回 `user_query=改写后`、透传路径写回 `user_query=原值`
- [x] 31. 联调验证路径2：改写只 1 轮收敛（detect r2 读改写后 query 通过），不再死循环到反问

## 后端增强（前置检查 LLM 化 + schema 空拒答）

- [x] 32. `src/rewrite/prompts.py`（或新建 `pre_reject_prompts.py`）新增前置拒答 LLM 判定 prompt：输入 user_query，输出 JSON `{reject: bool, category: "write_op"|"dangerous_info"|"normal", reason: str}`；few-shot 覆盖换皮写法 / 危险信息探查 / 正常查询
- [x] 33. `src/rewrite/pre_reject.py` 改造：保留空查询规则快路径；其余调 LLM 判定（`thinking=False`，`run_name="pre-reject"`），`reject=true` 设 `rejection_reason` + `rewrite_rejection_reason`；LLM 异常 / 不可用降级放行；state 写出 `pre_reject_category` 供 stage 透传
- [x] 34. `src/graph/main_graph.py` pre_reject 节点 `stage` 事件 payload 增加 `category`（从 state `pre_reject_category` 读取，透传到前端）
- [x] 35. `src/graph/main_graph.py` `make_schema_finalize_node`：`selected_schema` 为空时设 `rejection_reason="未在数据库中找到与查询相关的表或字段，请尝试换一种表述或确认数据范围"` + emit `schema_empty` 事件（带 reason）；route 仍 END
- [x] 36. `tests/rewrite/`（或 `tests/graph/`）pre_reject LLM 判定单测：mock LLM 返回各 category + reject；空查询规则快路径；LLM 降级放行
- [x] 37. `tests/graph/` schema 空拒答单测：`selected_schema=[]` 时设 `rejection_reason` + emit `schema_empty`

## 前端契约/状态/组件（schema 空拒答 + pre_reject category）

- [x] 38. `src/api/types.ts` 新增 `SchemaEmptyEvent`（reason）；`StageEvent` payload 可选 `category`；扩展 `SseEvent` 联合
- [x] 39. `src/store/types.ts` `TimelineNodeType` +`schema_empty`；`TurnDetails` +`schemaEmpty`；`preReject` +`category`
- [x] 40. `src/store/reducer.ts` 新增 `schema_empty` case（点 `schema_empty` 节点 error 态 + summary + `details.schemaEmpty`）；`stage(pre_reject)` 携带 `category` 时写入 `details.preReject.category`
- [x] 41. `src/components/AgentTimeline/index.tsx` `NODE_LABEL` / `NODE_ICON` +`schema_empty`（WarningOutlined）
- [x] 42. `src/components/DetailInspector/index.tsx` `renderDetail` +`schema_empty` case；`pre_reject` 详情展示 category（写操作 / 危险信息 / 通过）
- [x] 43. `frontend/tests/` reducer 单测：`schema_empty` 事件 -> 节点 + details；`stage(pre_reject, category)` -> `details.preReject.category`
- [x] 44. `npm run test` + 后端 pytest 全绿
- [x] 45. 联调验证：schema 全空查询 -> 时间轴出现 `schema_empty` 拒答节点 + 原因，无下游节点；危险信息查询 -> pre_reject 拒答 + category=危险信息

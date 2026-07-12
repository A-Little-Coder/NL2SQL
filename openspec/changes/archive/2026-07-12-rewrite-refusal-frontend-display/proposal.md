## Why

rewrite-v2（change `2026-07-12-rewrite-before-cache`）上线了 `pre_reject` 前置拒答节点与 Rewrite 子图（`detect_issues` ⇄ `rewrite_execute` ⇄ `clarify`），但这两个环节的**动作对用户完全不可见**：

1. **改写节点隐形**：`rewrite_execute` 已经 `emit_safe("rewrite", …)`（`src/rewrite/rewrite_subgraph.py:146`），但前端 `SseEvent` 联合类型未定义 `rewrite`、reducer 无对应 case、`mapStageNode("rewrite")` 返回 `null` 连 `stage` 事件都被丢弃。用户问句被改写后进下游，用户毫不知情。
2. **检测节点隐形**：`detect_issues` 完全不 emit，检测到的指代/歧义/缺失问题无处可看。
3. **拒答节点身份模糊**：`pre_reject` 通过时 `stage` 事件被 `mapStageNode` 丢弃；拒答时 `_wrap_node` 的 `stage done` 透传 `rejection_reason`，reducer 将其 upsert 为**通用 `error` 节点**（`reducer.ts:113-119`），与 answerability 拒答、decision 拒答、真实异常混在同一个 `error` 桶里，看不出是"前置拒答"。
4. **值改写 / 复用确认同样未接**：后端已 emit `value_rewrite`（`main_graph.py:265`）与 `cache_confirm`（`main_graph.py:360`）事件，前端 `default` 分支全部丢弃。

design.md R1 原本写明"`rewritten_query` 透传前端供用户确认"，但这一步从未接上。本 change 把改写/拒答/值改写/确认的动作在前台可见化（可观测，非可控）。

## What Changes

### 新增

- **前端消费 3 个已有但未接的 SSE 事件**：`rewrite`、`value_rewrite`、`cache_confirm`（后端零改动）。
- **后端 `detect_issues` 新增 emit `rewrite_detect` 事件**：携带 `round` / `has_issues` / `issue_detail` / `issue_types`，使"检测到什么问题"可见。
- **5 个新时间轴节点类型**：`pre_reject` / `rewrite_detect` / `rewrite` / `value_rewrite` / `cache_confirm`，各有中文标签、图标、Inspector 详情。
- **`TimelineNode.id` 字段 + upsert 按 id 优先**：支持 `detect_issues` / `rewrite_execute` 多轮各自独立节点（看清迭代），单次节点（cache/ir/cg 等）不受影响。
- **Inspector 多轮详情**：`rewrite` / `rewrite_detect` 详情按轮次数组呈现。

### 修改

- **`mapStageNode` 增加 `pre_reject` 映射**：`stage(pre_reject)` 点亮 `pre_reject` 节点。
- **reducer `stage` case 调整**：`pre_reject` 的 `rejection_reason` 归到 `pre_reject` 节点（红色拒答态），不再 upsert 通用 `error` 节点；其他节点的 `rejection_reason` 行为不变。
- **`TurnDetails` 新增字段**：`preReject` / `rewriteDetect` / `rewrite` / `valueRewrite` / `cacheConfirm`。
- **前置拒答升级为 LLM 语义判定**：`pre_reject` 从纯规则改为"空查询规则快路径 + LLM 语义判定"，LLM 判定 `增删改意图` / `危险信息指令` / `正常`，输出结构化 `{reject, category, reason}`；`stage(pre_reject)` payload 增加 `category`，前端 Inspector 展示类别。
- **schema 选择全空显式拒答**：`schema_finalize` 检测 `selected_schema` 为空时设 `rejection_reason` + emit 新 `schema_empty` 事件后 END，不再静默中断；前端新增 `schema_empty` 时间轴节点展示拒答原因。

### 删除

- 无。

## Capabilities

### Modified Capabilities
- `frontend-ui`：新增"前置拒答/改写检测/改写执行/值改写/复用确认可见"、"多轮节点独立呈现"、"前置拒答 LLM 判定类别可见"、"schema 空拒答节点可见"Requirements。
- `single-query-pipeline`：新增"SS 未选出表时显式拒答（不再静默 END）"Requirement。

## Impact

- **后端修改**：
  - `src/rewrite/rewrite_subgraph.py` - `make_detect_issues_node` 新增 `emit_safe("rewrite_detect", …)`；`rewrite_execute` 3 个 return 写回 `user_query`（D8 bug 修复）
  - `src/rewrite/pre_reject.py` - 纯规则改为空查询规则快路径 + LLM 语义判定（增删改 / 危险信息 / 正常），LLM 异常降级放行，写出 `pre_reject_category`
  - `src/rewrite/prompts.py`（或新建 `pre_reject_prompts.py`）- 前置拒答 LLM 判定 prompt
  - `src/graph/main_graph.py` - pre_reject 节点 `stage` 透传 `category`；`make_schema_finalize_node` 判空设 `rejection_reason` + emit `schema_empty`
- **前端修改**：
  - `src/api/types.ts` - 新增 `RewriteDetectEvent` / `RewriteEvent` / `ValueRewriteEvent` / `CacheConfirmEvent` / `SchemaEmptyEvent`，`StageEvent` payload 可选 `category`，扩展 `SseEvent` 联合
  - `src/store/types.ts` - `TimelineNode.id`、`TimelineNodeType` 新增 5 类 + `schema_empty`、`TurnDetails` 新增 5 字段 + `schemaEmpty`，`preReject` +`category`
  - `src/store/reducer.ts` - `mapStageNode` 加 `pre_reject`、`upsert` 改造为 id 优先、新增 4 + `schema_empty` 事件 case、`stage` case 调整 rejection_reason 归属 + 透传 category
  - `src/components/AgentTimeline/index.tsx` - `NODE_LABEL` / `NODE_ICON` 补全 5 类 + `schema_empty`、`key` 用 id
  - `src/components/DetailInspector/index.tsx` - `NODE_LABEL` 补全、`renderDetail` 新增 5 + `schema_empty` case、多轮列表渲染、pre_reject 展示 category
- **测试**：
  - 后端：`tests/` 补 `detect_issues` emit 断言、`rewrite_execute` 写回 user_query 断言、pre_reject LLM 判定单测（各 category + 降级）、schema 空拒答单测
  - 前端：`frontend/tests/` reducer 单测新增 5 + `schema_empty` 事件 case + 多轮 id + pre_reject 拒答不落通用 error + category 写入

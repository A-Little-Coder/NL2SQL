## Why

前台 `frontend-ui` 已上线，但实际使用中暴露三个透明度缺口与一个交互痛点：

1. **反问时前台同时冒出"错误"**：用户被 Agent 反问时，时间轴上却先蹦出一个红色错误节点，体验割裂。根因是后端 `_wrap_node` 装饰器把 `interrupt()` 抛出的 `GraphInterrupt` 当普通异常捕获并 emit 了 `error` 事件，而 `query.py` 随后又 emit 了正常的 `clarification` 事件——一次反问双发 error+clarification。

2. **IR 字段与字段值召回完全没展示**：后端 `RetrievedContext` 本有丰富的召回数据（`keyword_groups`、`keyword_columns_map`、`columns`、`values`），但 `_summarize_schema` 取了一个**不存在的字段** `ctx.schema_results`，导致 `schema_recall` 事件一直发空 `{"groups": []}`；而 LSH 值检索召回的 `values` 压根没有对应 SSE 事件。前端 `IrDetail` 收到的是空数据，用户看不到"关键词召回了哪些字段、哪些值"。

3. **SS（Schema Selection）阶段在时间轴上不可见**：后端 `single_query_graph` 注册了 `ss` 与 `schema_finalize` 节点并发 `stage` 事件，但前端 `mapStageNode` 没有 `ss` 的映射，事件被静默丢弃，时间轴从 IR 直接跳到可回答性/CG，SS 这一步对用户隐形。

4. **三栏布局不可调、边栏缩小时内容被压扁**：当前用 AntD `<Sider>` 固定宽度 + 二态折叠，无法按需拖拽调整左右栏宽度；折叠是直接缩到 0，中间态会把边栏内容压扁变形。

这四个问题共同削弱了"玻璃箱数据分析师"的核心价值——透明度。本 change 修复后端两处 bug、补齐 IR/SS 展示数据链路、并升级布局为可拖拽三栏。

## What Changes

**后端（`src/`）**：
- `_wrap_node` 装饰器放行 `GraphInterrupt`，不再 emit error——修复反问双发
- `retrieve_values` 在 `RetrievedItem.metadata` 记录 `source_phrase`/`source_term`，使每个召回值带准确的关键词组归属（而非扁平化丢弃）
- `_summarize_schema` 重构：基于 `RetrievedContext` 真实字段（`keyword_groups`/`keyword_columns_map`/`columns`/`values`）聚合，`schema_recall` 事件 payload 扩展为按关键词组组织的 `keyword_groups`（每组含 phrase/terms/召回字段/召回值）

**前端（`frontend/`）**：
- `mapStageNode` 增补 `ss`/`schema_finalize` -> 时间轴新增 SS 节点；`TimelineNodeType` 加 `ss` 类型
- `api/types.ts` 的 `SchemaRecallEvent` 扩展为 `keyword_groups` 结构；`store/types.ts` 的 `TurnDetails` 同步扩展
- `reducer.ts` 的 `schema_recall` 处理改为存 `keyword_groups` 聚合结构，新增 `schema_finalize` 事件处理
- `DetailInspector.IrDetail` 重构为「按关键词组聚合」视图：每组展示 phrase、同义词 terms、召回字段（带 score）、召回值（带 score，按后端 `source_phrase` 归属，前端零猜测）
- `AppLayout` 由 AntD `Sider` 改为 `react-resizable-panels`：三栏宽度可拖拽调整；边栏窄于阈值时不 mount children、只留展开条；宽度持久化到 localStorage

## Capabilities

### Modified Capabilities
- `information-retrieval`: 值检索结果标注来源关键词组（`source_phrase`/`source_term`），消除扁平化归属丢失
- `api-service`: `schema_recall` 事件 payload 重构为关键词组聚合结构；反问 `interrupt` 不再触发 `error` 事件
- `frontend-ui`: IR 节点按关键词组聚合展示字段与值召回；SS 阶段时间轴可见；三栏宽度可拖拽、窄边栏隐藏内容

## Impact

- **后端改动**：`src/graph/main_graph.py`（`_wrap_node`、`_summarize_schema`、IR 节点 emit）、`src/retrieval/information_retrieval.py`（`retrieve_values` 加 metadata 字段）。检索行为不变，仅多记归属字段，向后兼容。
- **前端改动**：`frontend/src/` 下 `api/types.ts`、`store/types.ts`、`store/reducer.ts`、`components/DetailInspector/`、`components/AppLayout.tsx`；新增依赖 `react-resizable-panels`。
- **测试**：后端 `retrieve_values` 归属字段单测、`_summarize_schema` 新结构单测、`_wrap_node` 放行 GraphInterrupt 单测；前端 reducer 的 `schema_recall`/`schema_finalize` 新结构单测、IrDetail/SsDetail 渲染单测、三栏拖拽交互测试。
- **契约兼容**：`schema_recall` payload 是破坏性变更（`groups` -> `keyword_groups`），但消费方只有本前端，同步更新即可；`error` 事件在反问场景不再出现，前端 `reducer` 已能处理"仅有 clarification 无 error"的路径。
- **非目标**：不动 IR 检索算法（LSH/语义精排逻辑不变）、不改 SS 选择算法、不做结果图表可视化、不做生产部署。

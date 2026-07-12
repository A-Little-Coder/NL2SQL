# enhance-ir-display-and-layout 实现任务清单

> 依赖：proposal.md、design.md（D1-D7）、specs/{information-retrieval,api-service,frontend-ui}/spec.md。
> 后端工程根：`src/`；前端工程根：`NL2SQL/frontend/`。

## 1. 后端：反问 error 双发修复（D1）

- [x] 1.1 `src/graph/main_graph.py` `_wrap_node` 的 `except Exception` 分支：try/except import `GraphInterrupt`，`isinstance(e, GraphInterrupt)` 时直接 `raise` 不 emit error（封装 `_is_graph_interrupt(e)` 兼容不同 langgraph 版本）
- [x] 1.2 补单测：模拟节点抛 `GraphInterrupt`，断言不 emit `error` 事件、异常正确 re-raise
- [x] 1.3 验证反问场景：cache_confirm / task_planner 反问时前端只收 `clarification` 不收 `error`

## 2. 后端：值检索记录组归属（D2）

- [x] 2.1 `src/retrieval/information_retrieval.py` `retrieve()` 构建 `term -> phrase` 映射，调整 `retrieve_values` 调用传入归属信息
- [x] 2.2 `retrieve_values` 在 `RetrievedItem.metadata` 写入 `source_phrase` 与 `source_term`；多 term 命中同一 value 时按 lsh_score 最高 term 所属 phrase 归属（覆盖更新）
- [x] 2.3 补单测：多关键词组检索后，每个 value 的 `metadata.source_phrase` 准确指向命中的组；多 term 命中按最高分归属
- [x] 2.4 跑既有 `retrieve_values` 相关测试，更新受影响断言（metadata 多字段）

## 3. 后端：schema_recall 事件重构（D3）

- [x] 3.1 `src/graph/main_graph.py` `_summarize_schema` 重构：基于 `ctx.keyword_groups`/`keyword_columns_map`/`columns`/`values` 聚合，输出 `{"keyword_groups": [{phrase, terms, columns, values}]}`
- [x] 3.2 values 过滤按 `metadata.source_phrase == phrase` 归属；columns 取 `keyword_columns_map[phrase]` 交集 `ctx.columns` 详情（含 score）
- [x] 3.3 同步更新 `src/api/routes/query.py` 的 SSE 事件 docstring（schema_recall payload 新结构）
- [x] 3.4 补单测：IR 节点 emit 的 schema_recall 事件含完整 keyword_groups，每组 columns/values 非空（有数据时）；空组保留且 columns/values 为空数组

## 4. 前端：契约与状态层同步（D3/D4）

- [x] 4.1 `frontend/src/api/types.ts` `SchemaRecallEvent` 重构：`data.keyword_groups: {phrase, terms, columns:[{table,column,score}], values:[{value,table,column,score}]}[]`；删除旧 `groups`
- [x] 4.2 `frontend/src/store/types.ts`：`TurnDetails` 增 `ir?: { keywordGroups: [...] }`（统一 keywords+schemaRecall 为 ir）；`TimelineNodeType` 加 `'ss'`；增 `schemaFinalize?: {joinEdges, bridgeTables}`
- [x] 4.3 `frontend/src/store/reducer.ts`：`mapStageNode` 增 `ss`/`schema_finalize` -> `'ss'` 映射；`case 'schema_recall'` 改存 `ir.keywordGroups` 聚合结构；新增 `case 'schema_finalize'` 存 `schemaFinalize` 并更新 ss 节点 summary
- [x] 4.4 `NODE_LABEL` 加 `ss: 'Schema 选择'`
- [x] 4.5 更新 `reducer.test.ts`：schema_recall 新结构、ss stage 映射、schema_finalize 处理

## 5. 前端：IR 详情按关键词组聚合展示（D5）

- [x] 5.1 `frontend/src/components/DetailInspector/index.tsx` 重构 `IrDetail`：逐关键词组渲染 Card，含 phrase / terms(Tag) / 召回字段(带 score) / 召回值(带 score)
- [x] 5.2 空组标注"无召回"；组数多时用 Collapse 折叠（默认展开首个）
- [x] 5.3 新增 `SsDetail`：展示 selected_schema 表/列数、join_edges、bridge_tables（来自 schema_finalize）
- [x] 5.4 `renderDetail` 分发增 `case 'ss'`
- [x] 5.5 补 `inspector.test.ts`：IrDetail 多组渲染、空组、SsDetail 渲染

## 6. 前端：三栏可拖拽布局（D6/D7）

- [x] 6.1 安装 `react-resizable-panels`（清华镜像源）
- [x] 6.2 `frontend/src/components/AppLayout.tsx`：AntD `Sider` -> `PanelGroup`+`Panel`+`PanelResizeHandle`；左 min80/max400 default280，右 min80/max600 default440，中 flex
- [x] 6.3 `PanelResizeHandle` 自定义竖向分隔条样式（hover 高亮、可拖拽光标）
- [x] 6.4 左右栏 `collapsible` + `collapsedSize=0`：拖到 min 以下折叠，条件渲染只留展开条（►/◄），children unmount
- [x] 6.5 `autoSaveId` 持久化宽度到 localStorage
- [x] 6.6 验证：边栏内容不被压扁（正常宽/折叠两态，无中间压扁态）
- [x] 6.7 补交互测试（vitest + jsdom 模拟拖拽/折叠）

## 7. 回归与文档

- [x] 7.1 前端 `npm run test` 全绿（reducer/inspector/sse/resume + 新增）
- [x] 7.2 后端 `pytest` 全绿（retrieve_values / _summarize_schema / _wrap_node + 既有）
- [x] 7.3 端到端验证：反问无双发 error、IR 详情见字段与值、SS 节点可见、三栏可拖拽且窄边栏隐藏
- [x] 7.4 更新 `frontend/README.md`（三栏拖拽用法、新依赖）与 `frontend/测试文案.md`（新增 IR/SS/布局测试场景）

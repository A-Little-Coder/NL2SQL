## Why

后端已暴露一条**会流式吐出完整推理链**的 NL2SQL Agent API（SSE 事件流含关键词/召回/可回答性/候选 SQL/执行结果/决策理由/qwen3 思考链，决策 49/50），多 DB 路由、会话、用户记忆、反问 resume 一应俱全，CORS 全开。但目前只有 Swagger，没有界面。

这套后端最值钱的不是"能查 SQL"，而是**透明**——市面上大多数 NL2SQL 是黑盒。一个能实时渲染这条推理流的前台，能把黑盒变成"玻璃箱数据分析师"，既可演示也给非工程师真正可用。现在 API 契约稳定、CORS 已开，是做前台的时机。

## What Changes

- 新增**独立前台工程** `NL2SQL/frontend/`（React + Vite + TypeScript + Ant Design + Zustand），不侵入现有 `src/` 后端代码
- **极透明三栏布局**：会话侧栏 ｜ 对话 + Agent 时间轴（常驻）+ 结果 ｜ 节点详情检查器（点哪个节点展开哪个节点的全部细节）
- **SSE 流式客户端**：逐事件渲染到时间轴/检查器；qwen3 思考链打字机式实时滚动；15s 心跳保活防超时
- **反问内联气泡 + resume 续流**：收到 `clarification` 事件中断 → 气泡展示问题/选项 → 用户作答 → 发带 `resume` 字段的 `POST /query` → 流继续
- **DB 下拉切换**：多 `db_id` 路由，首次访问冷库时提示"加载中"（后端懒加载 5-10s）
- **会话管理侧栏**：建/列/历史/删（对接 `POST/GET/DELETE /sessions`）
- **结果表格展示**：止步于表格（`result.result` 行数据 + 列头），不做图表、不做 SQL 手改
- **用户记忆视图**：展示 `GET /users/{id}/memory` 与 `/metrics`（术语偏好、常用表、指标定义等），贯彻透明主题
- **开发期 Vite proxy**：`:5173` → FastAPI `:8000`，规避跨域/凭据问题

## Capabilities

### New Capabilities
- `frontend-ui`: 前台问数交互界面——SSE 推理流实时渲染、反问 resume 闭环、多 DB 切换、会话管理、结果表格、用户记忆可视化

### Modified Capabilities
<!-- 前台只消费现有 REST + SSE 接口，不改变任何后端 spec 级行为，故无修改项 -->

## Impact

- **新增代码**：`NL2SQL/frontend/` 独立目录，与 `src/` 后端完全隔离，互不影响
- **新增工具链**：Node/npm（`package.json`），不进 Python `requirements.txt`
- **接口对接**：消费现有 REST（`/databases`、`/sessions`、`/users`）与 SSE（`/query`），**无后端改动**；CORS 已 `allow_origins=["*"]`
- **开发期**：Vite dev server `:5173` + proxy `/api/v1` → `localhost:8000`
- **部署**：v1 不涉及（out-of-scope，仅保证开发期可跑通；生产如何托管静态资源留待后续）

## Context

后端是一个 LangGraph 编排的 NL2SQL Agent，通过 FastAPI 对外提供：

- **REST**：`/health`、`/databases`、`/databases/{db_id}/tables`、`POST/GET/DELETE /sessions`、`GET /sessions/{id}/history`、`GET /users/{id}/memory`、`GET /users/{id}/metrics`
- **SSE**：`POST /api/v1/query`，`text/event-stream`，逐节点推送事件（`stage`/`cache_check`/`llm_thinking`/`keywords`/`schema_recall`/`answerability`/`sql_candidates`/`execution`/`final_decision`/`clarification`/`result`/`error`/`done`），所有事件 payload 带 `query_id`，每 15s 推 `: heartbeat` 注释行
- **多 DB 路由**：请求体带 `db_id`，`DbContextPool` 懒加载 + LRU，首次冷库 5-10s
- **反问 resume**：图可 `interrupt` 挂起，前端发带 `resume` 字段的新 `POST /query` 用 `Command(resume=...)` 恢复
- CORS 已 `allow_origins=["*"]`

当前无任何前台，只有 Swagger。本设计新建独立前台工程消费上述契约，**后端零改动**。

约束：CLAUDE.md 要求前台代码放 `NL2SQL/` 下独立目录、对话/文档用中文、生成可运行测试。

## Goals / Non-Goals

**Goals:**
- 极透明三栏布局，把 SSE 推理流实时渲染成"玻璃箱"体验
- 反问内联气泡 + resume 续流闭环，对用户呈现为"Agent 停下问一句，答完接着干"
- 多 DB 下拉切换 + 会话侧栏管理 + 结果表格 + 用户记忆视图
- 开发期一键可跑（Vite `:5173` proxy -> FastAPI `:8000`）

**Non-Goals:**
- 不做图表/数据可视化（止步表格）
- 不做 SQL 手改/重跑 workbench
- 不做生产部署托管（v1 仅保证开发期跑通）
- 不做用户认证/多用户隔离（`user_id` v1 固定或简单输入）
- 不改后端任何代码
- 不做 SSE HTTP 层断线重连（`resume` 是图级语义，由用户主动触发，非 HTTP 自动重连）

## Decisions

### D1 技术栈：React 18 + Vite + TypeScript + Ant Design + Zustand

- **React + Vite**：SSE/流式生态最成熟，Vite dev 即时热更、配置简单
- **TypeScript**：与后端 Pydantic schema 对齐，`api/types.ts` 作单一契约源，编译期拦下事件字段错配
- **Ant Design**：中文 locale 内置、`Table`/`Layout`/`Tree` 等组件业内最强，信息密集的三栏布局开箱即用；契合"表格为主 + 中文语境 + 快速出效果"
- **Zustand**：轻量无样板，适合高频流式更新（思考链打字机、节点逐个点亮）；比 Redux Toolkit 轻、比 Context 在频繁更新下性能好

**备选**：Vue+Vite（生态略窄）、Next.js（后端已是 FastAPI，SSR/Routing 用不上，过重）、HTMX（复杂时间轴/检查器交互力不从心）。组件库备选 shadcn/ui+Tailwind（更现代但表格/侧栏需自组装，工期更长）。

### D2 SSE 客户端：fetch + ReadableStream 自写解析器（非 EventSource）

- 原生 `EventSource` 只支持 GET、不能带 JSON body；而 `/query` 是 POST + JSON body，必须用 `fetch`
- 自写行解析器：按 `\n\n` 切事件块；`data: ` 前缀取 JSON payload；`:` 前缀为注释行（心跳），收到即重置客户端读超时计时器
- `AbortController` 支持用户取消；`fetch` 流式读 `response.body.getReader()`

**备选**：`@microsoft/fetch-event-source`（功能更全、支持自动重连，但本场景每次查询是新流、不需 HTTP 层重连，多一个依赖不划算）。

### D3 状态结构：per-query 状态 + Zustand 全局 store

```
store
├─ sessions: SessionSummary[]          // GET /sessions/{user}
├─ currentSessionId: string
├─ dbList: DatabaseInfo[]              // GET /databases
├─ selectedDbId: string
├─ userMemory / userMetrics            // GET /users/{id}/memory|metrics
└─ turns: Turn[]                       // 当前会话的对话轮次

Turn（一个用户问题及其全部后续，含反问 resume）
├─ turnId: string                      // 客户端生成，跨 resume 稳定（见 D4）
├─ userQuery: string
├─ timeline: TimelineNode[]            // 有序：cache/ir/answerability/cg/execution/decision/clarify
├─ details: {                          // 按节点类型存结构化产物
│    keywords, schema_recall, answerability,
│    candidates, exec: {by candidate_id},
│    decision, cache }
├─ thinking: { [node]: string }        // qwen3 思考链按节点累积
├─ result: { sql, rows } | null
├─ status: 'streaming'|'done'|'error'|'awaiting_clarification'
├─ selectedNode: string | null         // 检查器当前显示节点，null=自动跟随最新
└─ clarification: { question, ambiguities, round } | null
```

`useQueryStream(turnId)` hook 订阅 SSE，把事件 reduce 进对应 `Turn`。

### D4 反问 resume 的 turn 主键用客户端 turnId，不用 server queryId

后端每次 `POST /query`（含 resume）都生成新 `query_id`，但图通过 `thread_id=session_id` 恢复 checkpoint，逻辑上是同一轮的延续。若用 `query_id` 作主键，resume 流会变成"新的一条消息"，割裂体验。

**决策**：前端生成 `turnId`（UUID）作 turn 主键，贯穿初始查询与其后的 resume 流。resume 请求的新 SSE 流事件**并入同一 `Turn`**：时间轴在 `clarification` 节点之后继续追加，检查器/结果区共用。server 的 `query_id` 仅用于事件分组日志，不参与 UI 主键。

### D5 节点详情检查器：自动跟随 + 点击 pin

- 默认 `selectedNode=null` -> 自动跟随最新完成的节点
- 用户点时间轴某节点 -> `selectedNode=<node>`，右栏锁定显示该节点详情（候选 SQL 全文/执行结果/思考链/决策理由）
- 新查询开始 -> 重置为 null，重新自动跟随

### D6 反问气泡内联渲染

`clarification` 事件到达时，`Turn.status='awaiting_clarification'`，时间轴停在"反问"节点，节点下方内联气泡展示 `question` + `ambiguities`（可点击选项或文本输入）。用户作答即触发 resume 请求。**不用模态弹窗**--保持对话流的连续感（已与用户确认选内联气泡）。

### D7 结果表格

`result.result` 为 `list[dict]`。取首行 keys 作列，AntD `Table` 渲染，分页（默认 10/页）。SQL 用代码块 + 复制按钮，置于表格上方。不做图表、不做手改重跑。

### D8 开发期 Vite proxy

`vite.config.ts` 配 `server.proxy['/api/v1'] -> http://localhost:8000`，前端直连同源 `/api/v1/...`，规避跨域与凭据问题。CORS 虽已开，proxy 更省事且贴近生产同源部署形态。

### D9 目录结构

```
NL2SQL/frontend/
├─ package.json  vite.config.ts  tsconfig.json  index.html
├─ src/
│  ├─ main.tsx  App.tsx
│  ├─ api/
│  │  ├─ types.ts          # 镜像后端 schema + 全部 SSE 事件类型（单一契约源）
│  │  ├─ rest.ts           # databases/sessions/memory（fetch 封装）
│  │  └─ sse.ts            # fetch-based SSE 解析器（data:/: heartbeat）
│  ├─ store/useChatStore.ts     # Zustand：sessions/turns/db/memory
│  ├─ hooks/useQueryStream.ts   # 订阅 SSE -> reduce 进 Turn
│  └─ components/
│     ├─ SessionSidebar/  DbSelector/  Conversation/
│     ├─ AgentTimeline/        # 常驻时间轴
│     ├─ DetailInspector/      # 右侧分栏（D5）
│     ├─ ClarificationBubble/  # 内联反问（D6）
│     └─ ResultTable/  SqlBlock/
└─ tests/                     # Vitest 单测：SSE 解析器、reducer、resume 合并
```

### D10 测试策略：Vitest 单元 + Playwright MCP 交互验证

前端测试分两层：

- **单元测试（Vitest）**：覆盖逻辑层——SSE 解析器、reducer、resume 合并、检查器选中态。运行快，无浏览器依赖，`npm run test` 执行。
- **交互验证（Playwright MCP）**：覆盖浏览器渲染层——开发期间用 Claude Code 的 Playwright MCP 工具（`browser_navigate`/`click`/`type`/`snapshot`/`screenshot`）逐场景手动验证前端界面功能。浏览器为 Edge（`--browser msedge`），无需在 `frontend/` 内额外安装 `@playwright/test` 依赖或配置 `playwright.config.ts`。

验证方式：开发完每个功能模块后，启动后端（`python run_api.py`）和前端（`npm run dev`），用 MCP 工具操作 Edge 浏览器，按 tasks.md 中定义的验证场景逐一检查渲染结果、交互行为、SSE 事件流响应。验证结果即验收标准。

## Risks / Trade-offs

- **[SSE 被代理/浏览器缓冲导致不实时]** -> 后端已设 `X-Accel-Buffering: no` + 15s 心跳；前端用 `fetch` streaming 读 `getReader()`，不用 XHR
- **[冷库首次加载 5-10s，用户以为卡死]** -> `acquire` 期间明确 loading 态 + 文案"首次加载该数据库，约需数秒…"；时间轴已点亮的节点保持可见
- **[resume 跨 query_id 合并状态易错]** -> D4：客户端 `turnId` 作主键，server `query_id` 不参与 UI 主键；单测覆盖"初始流 + resume 流合并到同一 Turn"
- **[极透明信息过载]** -> 时间轴节点紧凑（图标+一行摘要），详情默认折叠在检查器；用户不点开也能看到最终 SQL+表
- **[AntD 包体积偏大]** -> Vite 默认 tree-shake + 按需引入；v1 可接受，后续可按需优化
- **[用户作答 resume 后图仍可能再次反问（多轮）]** -> `clarification.round` 透传，气泡支持多轮，turn 内可多次 resume

## Migration Plan

全新独立工程，**无迁移**：`NL2SQL/frontend/` 与 `src/` 后端完全隔离，后端零改动。回滚 = 删除 `frontend/` 目录，不影响后端任何行为。开发期启动：`python run_api.py`（:8000）+ `cd frontend && npm run dev`（:5173）。

## Open Questions

- **`user_id` 来源**：v1 固定 `"default"`，还是侧栏加一个简单输入框？建议 v1 固定 `default` + 顶部可改输入框，待后续接认证体系再定。（不阻塞开发）
- **生产托管**：v1 不涉及。后续可选项--FastAPI 挂静态目录、或独立 nginx。留待 v2。

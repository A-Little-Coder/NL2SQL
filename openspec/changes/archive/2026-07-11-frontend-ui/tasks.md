# frontend-ui 实现任务清单

> 依赖：proposal.md（动机/范围）、design.md（技术决策 D1-D9）、specs/frontend-ui/spec.md（可测场景）。
> 技术栈：React 18 + Vite + TypeScript + Ant Design + Zustand。工程根：`NL2SQL/frontend/`。

## 1. 工程脚手架

- [x] 1.1 在 `NL2SQL/frontend/` 初始化 Vite + React + TS 工程（`package.json`、`vite.config.ts`、`tsconfig.json`、`index.html`、`src/main.tsx`）
- [x] 1.2 安装依赖：`antd`、`zustand`、`@ant-design/icons`、`vitest`、`@testing-library/react`、`jsdom`（用清华镜像源）
- [x] 1.3 配置 `vite.config.ts`：dev server `:5173` + proxy `/api/v1` -> `http://localhost:8000`；Vitest 测试环境 jsdom
- [x] 1.4 配置 AntD 中文 locale 与 `ConfigProvider`，挂载根 `App.tsx`，验证 `npm run dev` 可起、`/api/v1/health` 经 proxy 可达
- [x] 1.5 在项目根 `.gitignore` 增补 `frontend/node_modules/`、`frontend/dist/`

## 2. API 契约层（单一契约源）

- [x] 2.1 `src/api/types.ts`：镜像后端 Pydantic schema（`QueryRequest`/`CreateSessionRequest`/`SessionSummary`/`UserMemoryResponse`/`DatabaseInfo` 等）
- [x] 2.2 `src/api/types.ts`：定义全部 SSE 事件类型联合（`SseEvent = stage | cache_check | llm_thinking | keywords | schema_recall | answerability | sql_candidates | execution | final_decision | clarification | result | error | done`），字段与 `query.py` 文档一致
- [x] 2.3 `src/api/rest.ts`：封装 `fetch` 调用——`listDatabases`/`listTables`/`createSession`/`listSessions`/`getSessionHistory`/`deleteSession`/`getUserMemory`/`getUserMetrics`/`getHealth`
- [x] 2.4 `src/api/sse.ts`：实现 fetch + ReadableStream 的 SSE 解析器（按 `\n\n` 切块、`data:` 取 JSON、`:` 识别心跳），导出 `streamQuery(body, { onEvent, signal })`

## 3. 状态层（Zustand store + reducer）

- [x] 3.1 `src/store/useChatStore.ts`：定义全局状态（`sessions`/`currentSessionId`/`dbList`/`selectedDbId`/`userMemory`/`userMetrics`/`turns`/`userId`）
- [x] 3.2 定义 `Turn` 与 `TimelineNode` 类型，实现 `reduceSseEvent(turn, event) -> Turn` 纯函数（处理全部事件类型，含 `clarification` 设 `awaiting_clarification`、`done` 收尾、`rejection` 标记）
- [x] 3.3 实现 `turnId` 客户端生成逻辑（D4）：初始查询与 resume 共用同一 `turnId`，server `query_id` 仅存日志不参与主键
- [x] 3.4 实现检查器选中态：`selectedNode`（null=自动跟随最新，点击 pin，新查询重置 null，见 D5）

## 4. SSE 订阅 Hook

- [x] 4.1 `src/hooks/useQueryStream.ts`：封装"发送初始查询"与"发送 resume"两个动作，调用 `streamQuery`，把事件 reduce 进对应 `turnId` 的 `Turn`
- [x] 4.2 处理 `AbortController` 取消、网络错误降级为 `error` 事件并入 Turn
- [x] 4.3 resume 动作：携带 `resume=<回答>` 与原 `session_id`/`db_id`/`user_id`，新流事件并入同一 `turnId`

## 5. 三栏布局与导航

- [x] 5.1 `src/components/AppLayout.tsx`：AntD `Layout` 三栏（Sider 会话侧栏 / Content 对话区 / Sider 右侧检查器），右侧栏可折叠
- [x] 5.2 顶部栏：`user_id` 输入（v1 默认 `default`）+ 视图切换（对话 / 用户记忆）
- [x] 5.3 加载初始数据：`listDatabases` + `listSessions` 填充 store，`selectedDbId` 默认取首个

## 6. 会话侧栏

- [x] 6.1 `src/components/SessionSidebar/`：渲染会话列表（`updated_at` 降序，含 `turn_count`、`status`）
- [x] 6.2 "新会话"按钮 -> `createSession` 并切换
- [x] 6.3 点击会话 -> `getSessionHistory` 加载历史轮次到对话区
- [x] 6.4 删除会话 -> `deleteSession`，删除当前会话则切首个或空态
- [x] 6.5 查询完成后若 `session_id` 为新建，刷新侧栏列表（场景：未知 session_id 自动创建）

## 7. DB 选择器

- [x] 7.1 `src/components/DbSelector/`：下拉列出 `dbList`，切换更新 `selectedDbId`
- [x] 7.2 后续查询携带当前 `selectedDbId`
- [x] 7.3 404（db 不存在）友好提示，选择回退到上一有效值

## 8. 对话区与 Agent 时间轴

- [x] 8.1 `src/components/Conversation/`：渲染 `turns`（用户消息 + 助手 Turn 卡片）
- [x] 8.2 `src/components/AgentTimeline/`：在助手卡片内渲染常驻时间轴，节点按 `status` 点亮，每节点一行摘要（命中/关键词/候选数/可回答性/决策）
- [x] 8.3 缓存命中短路：仅渲染缓存节点 + 结果（跳过 ir/ss/cg）
- [x] 8.4 rejection / error 节点展示理由，不渲染结果表
- [x] 8.5 底部输入框：回车发送查询（携带 `session_id`/`user_id`/`db_id`），发起新 Turn

## 9. 节点详情检查器

- [x] 9.1 `src/components/DetailInspector/`：根据 `selectedNode`（或自动跟随的最新节点）渲染对应详情
- [x] 9.2 按节点类型渲染：`keywords`/`schema_recall` 分组、`sql_candidates` 候选列表全文、`execution` 每候选结果、`final_decision` 选中理由、`answerability` 判断
- [x] 9.3 点击时间轴节点 pin、新查询重置自动跟随

## 10. qwen3 思考链

- [x] 10.1 检查器内思考链区域：按节点累积 `llm_thinking.text`，打字机式滚动，默认折叠可展开
- [x] 10.2 无 `llm_thinking` 事件时不显示空区域（非思考模型降级）

## 11. 反问内联气泡与 resume

- [x] 11.1 `src/components/ClarificationBubble/`：`awaiting_clarification` 时在反问节点下方内联展示 `question` + `ambiguities`（可点选/输入）
- [x] 11.2 用户作答 -> 触发 resume 动作（4.3），事件并入同 `turnId`，时间轴在反问节点后继续追加
- [x] 11.3 支持多轮反问（`round` 递增，可多次 resume）
- [x] 11.4 反问期间不渲染结果表，直至 resume 流推 `result`

## 12. 结果表格

- [x] 12.1 `src/components/ResultTable/`：`result` 行列表取首行 keys 作列，AntD `Table` 分页（默认 10/页）
- [x] 12.2 `src/components/SqlBlock/`：SQL 代码块 + 复制按钮（写剪贴板 + 成功反馈），置于表格上方
- [x] 12.3 空结果显示"无数据"占位，SQL 仍展示

## 13. 用户记忆视图

- [x] 13.1 `src/components/UserMemoryView/`：调 `getUserMemory`，分区块展示 `term_preferences`/`frequently_used_tables`/`metric_definitions`/`query_preferences`/`domain_context`/`clarification_history`
- [x] 13.2 调 `getUserMetrics` 展示指标定义列表（`name`/`description`/`sql_pattern`/`source`/`confidence`）
- [x] 13.3 `user_id` 切换时刷新记忆与指标

## 14. 测试（Vitest，必须通过）

- [x] 14.1 `tests/sse.test.ts`：SSE 解析器——`data:` JSON 解析、`: heartbeat` 识别不产事件、多事件块切分
- [x] 14.2 `tests/reducer.test.ts`：`reduceSseEvent` 各事件类型 -> Turn 状态正确（含 cache 命中短路、clarification 设 awaiting、rejection 标记、done 收尾）
- [x] 14.3 `tests/resume.test.ts`：初始流 + resume 流合并到同一 `turnId`，server `query_id` 变化不影响 `turnId`
- [x] 14.4 `tests/inspector.test.ts`：自动跟随 / 点击 pin / 新查询重置 null
- [x] 14.5 `npm run test` 全绿

## 15. Playwright MCP 交互验证（开发期逐场景验证）

验证前提：后端 `python run_api.py` 运行在 `:8000`，前端 `npm run dev` 运行在 `:5173`。
使用 Claude Code 的 Playwright MCP 工具（`browser_navigate`/`click`/`type`/`snapshot`/`screenshot`）操作 Edge 浏览器。

- [x] 15.1 **三栏骨架渲染**：`navigate` 到 `localhost:5173` → `snapshot` 检查侧栏 / 对话区 / 右侧检查器 / DB 选择器 / 输入框均存在
- [x] 15.2 **DB 下拉加载**：`snapshot` 检查下拉选项包含 `GET /databases` 返回的 db_id 列表 → 切换一个 db → 验证后续查询使用新 db_id
- [x] 15.3 **提交查询 → 时间轴点亮**：`type` 输入查询 → `click` 提交 → 每 2-3s `snapshot` 检查时间轴节点逐个点亮
- [x] 15.4 **结果表格 + SQL 展示**：等待 `result` 事件 → `snapshot` 检查表格列头与行数据、SQL 代码块与复制按钮
- [x] 15.5 **反问内联气泡 + resume**：构造会触发 `clarification` 的查询 → `snapshot` 检查气泡展示 `question`+`ambiguities` → `click` 选项 → 验证时间轴在反问节点后继续追加
- [x] 15.6 **会话管理**：`click` "新会话" → `snapshot` 检查侧栏新增 → `click` 历史会话 → 检查历史轮次加载 → `click` 删除 → 检查侧栏移除
- [x] 15.7 **用户记忆视图**：切换视图到"记忆" → `snapshot` 检查 `term_preferences`/`frequently_used_tables`/`metric_definitions` 等各区块
- [x] 15.8 **冷库加载提示**：切到未加载的 DB → 提交查询 → `snapshot` 检查"首次加载约需数秒"提示文案
- [x] 15.9 **缓存命中短路**：重复提交同一查询 → `snapshot` 检查时间轴仅显示缓存节点 + 结果（跳过 ir/ss/cg/execution）
- [x] 15.10 在 `frontend/README.md` 写启动说明（依赖、dev、proxy、测试命令），中文

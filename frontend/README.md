# NL2SQL 前台问数助手

基于 React 18 + Vite + TypeScript + Ant Design + Zustand 的前台界面，消费后端 NL2SQL Agent 的 REST + SSE 接口，把 Agent 推理流实时渲染成"玻璃箱数据分析师"体验。

## 技术栈（决策 D1）

- **React 18 + Vite**：SSE/流式生态成熟，dev 即时热更
- **TypeScript**：与后端 Pydantic schema 对齐，`src/api/types.ts` 作单一契约源
- **Ant Design 5**：中文 locale 内置，`Table`/`Layout`/`Timeline` 等组件开箱即用
- **Zustand**：轻量状态管理，适合高频流式更新
- **react-resizable-panels**：三栏可拖拽布局（宽度调整 + 折叠 + localStorage 持久化）
- **Vitest**：单元测试（SSE 解析器、reducer、resume 合并、检查器选中态）

## 目录结构（决策 D9）

```
frontend/
├─ package.json  vite.config.ts  tsconfig.json  index.html
├─ src/
│  ├─ main.tsx  App.tsx
│  ├─ api/
│  │  ├─ types.ts          # 后端 schema + 全部 SSE 事件类型（单一契约源）
│  │  ├─ rest.ts           # databases/sessions/memory 的 fetch 封装
│  │  └─ sse.ts            # fetch + ReadableStream 的 SSE 解析器
│  ├─ store/
│  │  ├─ types.ts          # Turn / TimelineNode / TurnDetails 等状态类型
│  │  ├─ reducer.ts        # reduceSseEvent 纯函数 + createTurn
│  │  └─ useChatStore.ts   # Zustand 全局 store + turnId 生成
│  ├─ hooks/useQueryStream.ts   # SSE 订阅 hook（初始查询 / resume / 取消）
│  └─ components/
│     ├─ AppLayout.tsx           # 三栏布局骨架
│     ├─ SessionSidebar/         # 会话侧栏（建/列/历史/删）
│     ├─ DbSelector/             # 多数据库下拉切换
│     ├─ Conversation/           # 对话区 + 底部输入框
│     ├─ AgentTimeline/          # 常驻推理时间轴
│     ├─ DetailInspector/        # 节点详情检查器 + qwen3 思考链
│     ├─ ClarificationBubble/    # 反问内联气泡 + resume
│     ├─ ResultTable/  SqlBlock/ # 结果表格 + SQL 复制
│     └─ UserMemoryView/         # 用户记忆可视化
└─ tests/                  # Vitest 单测
```

## 环境依赖

- Node.js ≥ 18（推荐 20+）
- npm
- 后端服务运行在 `http://localhost:8000`（`python run_api.py`，见项目根 README）

## 安装

在 `frontend/` 目录下执行（使用清华镜像源加速）：

```bash
npm install --registry https://registry.npmmirror.com
```

## 开发启动

1. 启动后端（项目根目录）：

   ```bash
   python run_api.py
   ```

   后端监听 `:8000`，CORS 已全开。

2. 启动前端 dev server（`frontend/` 目录）：

   ```bash
   npm run dev
   ```

   前端监听 `:5173`，自动打开浏览器访问 `http://localhost:5173`。

## Vite Proxy（决策 D8）

`vite.config.ts` 配置了 dev proxy：

```
/api/v1  ->  http://localhost:8000
```

前端所有请求走同源 `/api/v1/...`，由 Vite 转发到 FastAPI，规避跨域/凭据问题。

## 测试

```bash
npm run test          # 单次运行全部单测
npm run test:watch    # watch 模式
```

测试覆盖（决策 D10）：
- `tests/sse.test.ts`：SSE 解析器（data: JSON / : heartbeat / 多事件块切分）
- `tests/reducer.test.ts`：reduceSseEvent 各事件类型 -> Turn 状态（cache 短路、clarification、rejection、done 收尾）
- `tests/resume.test.ts`：初始流 + resume 流合并到同一 turnId，server query_id 变化不影响 turnId
- `tests/inspector.test.ts`：检查器选中态（自动跟随 / 点击 pin / 新查询重置）
- `tests/ir_ss_detail.test.tsx`：IrDetail 按关键词组聚合渲染（phrase/同义词/字段/值）、SsDetail（join_edges/bridge_tables）
- `tests/AppLayout.test.tsx`：CollapsedBar 折叠展开条交互

## 构建

```bash
npm run build     # tsc 类型检查 + Vite 生产构建，产物在 dist/
npm run preview   # 本地预览生产构建
```

## 关键设计

- **极透明三栏布局**：会话侧栏 ｜ 对话 + Agent 时间轴 + 结果 ｜ 节点详情检查器；左右栏宽度可拖拽调整、窄于阈值折叠为展开条（react-resizable-panels，宽度持久化到 localStorage）
- **IR 详情按关键词组聚合**：每组展示 phrase / 同义词 / 召回字段（带 score）/ 召回值（带 score，后端 source_phrase 归属，前端零猜测）；SS 阶段独立时间轴节点（schema_finalize 更新 JOIN 边/桥接表）
- **SSE 流式渲染**：fetch + ReadableStream 自写解析器（决策 D2），逐事件点亮时间轴
- **反问 resume 续流**：客户端 turnId 作主键（决策 D4），resume 流并入同一 Turn
- **检查器自动跟随 + 点击 pin**（决策 D5）：默认跟随最新节点，点击锁定
- **qwen3 思考链**：按节点累积 llm_thinking，打字机式滚动，默认折叠

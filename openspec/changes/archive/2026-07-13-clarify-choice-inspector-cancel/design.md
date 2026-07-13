## Context

NL2SQL 流式问数链路：React+Vite+AntD+Zustand 前端（`frontend/`）通过 fetch+ReadableStream 消费 FastAPI 后端（`src/api/`）的 SSE 事件流；后端 `graph.stream` 跑在 `run_in_executor` 的后台线程里，事件经 `asyncio.Queue` 跨线程传给 SSE 生成器。三个独立缺口均落在这条链路上：

- 反问气泡（`ClarificationBubble`）已支持把 `ambiguities` 渲染为可点击 Tag，但 `cache_confirm` 节点下发 `ambiguities: []`，只剩自由输入；后端 `approved = user_choice in {"复用","是","yes",...}` 是精确集合匹配，synonym 全部失败。
- 详情检查器（`DetailInspector`）`currentTurn = turns[turns.length-1]` 硬编码读最后轮；`AgentTimeline.handleClick` 写入的 `selectedNode` 落在对应 turn 上，但检查器不读旧轮。
- `useQueryStream.cancel()` 已实现 `AbortController.abort()` 但无 UI 调用；abort 后 Turn 无人置终态，停留 `streaming`；后端无 `request.is_disconnected()` 检测，后台线程不知客户端已走，跑完整条图。

## Goals / Non-Goals

**Goals:**
- 反问从"自由输入 + 字符串匹配"改为"结构化选项 + 类型化判定"，`cache_confirm` 二选一按钮即提交。
- 检查器可回看任意历史轮的中间节点，且不破坏"自动跟随最新"的现有体验。
- 用户可一键终止在途请求：前端断流 + Turn 进 `cancelled` 终态（无僵尸），后端合作式停止后台线程。
- `learn/cancel-stream/` 最小可运行 demo，逐步讲清 SSE 断流与线程合作式取消原理。

**Non-Goals:**
- 不改 LangGraph 节点业务逻辑（仅改 `cache_confirm` payload 与 `query.py` 流循环）。
- 不实现"节点中途强制中断"--Python 线程不可 kill，取消只在节点边界生效。
- 不改会话历史持久化结构（`setHistoryTurns` 构造的历史 turn 不参与 `inspectorTurnId`）。
- 不做多请求并发取消调度（单请求级取消，每请求独立 `cancel_event`）。
- 不引入新外部依赖（`threading.Event` / `AbortController` / `request.is_disconnected()` 均为现有运行时能力）。

## Decisions

### D1: clarification 三态结构化（`kind` + `options`）

clarification 事件 payload 新增 `kind: "confirm" | "choice" | "open"` 与 `options: {label, value}[]`。前端 `ClarificationBubble` 按 `kind` 分发渲染：

| kind | 渲染 | 输入框 | 提交 |
|---|---|---|---|
| `confirm` | 两个主按钮（是/否） | 隐藏 | 点按钮发对应 `value` |
| `choice` | 按钮组（`options`） | 可选（自定义） | 点按钮发 `value`；输入框发原文 |
| `open` | 纯输入框（现有行为） | 显示 | 发原文 |

`cache_confirm` 节点 payload 改为 `kind="confirm"`, `options=[{label:"是，复用",value:"yes"},{label:"否，重新生成",value:"no"}]`，`ambiguities` 保留为空（兼容）。后端 `approved = (user_choice == "yes")`，同时保留旧字符串集合匹配作为兜底（`value` 不在 `{"yes","no"}` 时回退，兼容旧前端/测试逃逸）。

**Alternatives:**
- (A) 仅填 `ambiguities=["是","否"]`，零前端改动。**否决**：输入框仍在，synonym 问题未根治；无类型化语义，后续反问仍脆弱。
- (B，选定) 结构化 `kind`/`options`，按钮发 `value`，后端按结构判定。

**Open**：`value` 用 `"yes"/"no"` 还是 `"是"/"否"`。倾向 `"yes"/"no"`（后端 `approved = value=="yes"` 简洁），旧集合匹配兜底兼容中文输入。实现时确认。

### D2: `inspectorTurnId` 提升到 store 顶层

store 顶层新增 `inspectorTurnId: string | null`（`null` = 自动跟随最新 turn，与 `selectedNode` 的 null 语义对称）。行为：

- `selectNode(turnId, node)`：设 `turn.selectedNode = node` **且** `inspectorTurnId = turnId`（pin 到该 turn）。
- 点击当前已选中节点：`inspectorTurnId = null` + `selectedNode = null`（全自动跟随）。
- 新增 `releaseInspector()`：仅置 `inspectorTurnId = null`（保留各 turn 的 `selectedNode`）。
- `DetailInspector`：`turn = turns.find(inspectorTurnId) ?? turns[last]`。
- 新轮开始（`startTurn`）：若 `inspectorTurnId === null`，自动跟到新轮（现有行为）；若 pin 在旧轮，保持旧轮，检查器顶部显示"📌 已锁定到第 N 轮 · {node}"+"返回最新"按钮。
- `AgentTimeline`：被 pin 的 turn 时间轴加视觉标记（如左侧色条），提示"检查器正查看此轮"。

**Alternatives:**
- (a) 每个 turn 自带 inspector 视图。**否决**：三栏布局下单例检查器更一致，避免 N 份滚动容器。
- (b) 命名 `selectedTurnId`。等价，选 `inspectorTurnId` 更语义化。

历史 turn（`setHistoryTurns` 构造的 `history-*` id）同样可 pin（中间节点少，但 result/error 可查）。

### D3: 请求终止--前端断流 + 后端合作式取消

**前端：**
- `Conversation` 在 `sending` 期间显示"停止"按钮（替换或并排发送按钮），点击调 `cancel()` + 新增 `cancelTurn(turnId)`。
- `cancelTurn(turnId)`：`status = 'cancelled'`，timeline 追加节点 `{type:'error', status:'done', summary:'用户已取消'}`，置 `error = '用户已取消请求'`、`rejection = false`、新增 `cancelled = true` 标记。
- `TurnStatus` 新增 `'cancelled'`。`AssistantCard`：`cancelled` 态显示"已取消"提示，不显示"推理中"loading，不渲染结果表。
- `useQueryStream.cancel()`：abort 后调 `cancelTurn`（修复僵尸 streaming）。

**后端 `query.py`：**
- 闭包内 `cancel_event = threading.Event()`，传入 `run_graph`。
- `for update in graph.stream(...)` 循环体处理完每个 update 后 `if cancel_event.is_set(): logger.info(...); break`。
- `event_stream` 断连检测两路：(1) 心跳 `asyncio.TimeoutError` 分支 `await request.is_disconnected()` 为真则 `cancel_event.set()` + `break`；(2) `except (asyncio.CancelledError, GeneratorExit)` 中 `cancel_event.set()` + `raise`。
- `finally`：`cancel_event.set()`（兜底）+ `pool.release`。
- `break` 后跳过 result/done 推送（连接已断，推了无人收），直接走 release。

**Alternatives:**
- (a) `POST /api/v1/query/cancel` + `query_id -> cancel_event` 注册表。**否决**：需额外端点、注册表、超时清理；客户端断连检测已能触发取消，无需显式调用。
- (b) LangGraph 原生 cancellation config。**否决**：版本相关、行为不稳；`threading.Event` 合作式更可控、版本无关。

**限制（文档明示）**：取消在节点边界生效--当前节点（含进行中的 LLM 调用）跑完后线程才退出。心跳间隔 15s 内感知断连。长节点（多轮修复）可能多跑一会。

### D4: `learn/cancel-stream/` 最小可运行复刻

剥离业务，保留"前端 SSE + 后台线程 + 取消信号"骨架：
- `backend.py`：FastAPI SSE，N 步假流水线（每步 sleep 2s），`threading.Event` + 节点边界检查 + `is_disconnected` 检测。
- `frontend.html`：原生 JS `fetch` + `ReadableStream` 解析 SSE + `AbortController` + 开始/停止按钮，实时打印进度。
- `README.md` + 5 篇 `steps/`（sse-basics / abort-fetch / server-detect / thread-cancel / node-boundary）+ `run.md` 观察清单。

## Risks / Trade-offs

- **[取消非即时]** 当前节点跑完才停。-> 文档与 learn demo 明示节点边界语义；心跳 15s 内检测断连；可接受（节点是原子的，不留脏 state）。
- **[clarification schema 扩展 BREAKING]** 新增 `kind`/`options` 字段。-> 字段可选，旧客户端忽略仍工作；`kind` 缺失时前端按 `open` 兜底（现有行为）；`cache_confirm` 行为变化在 proposal documented。
- **[pin 后新轮事件不自动刷检查器]** -> 设计如此（pin 即锁定）；提供"返回最新"按钮；不 pin 则自动跟随，符合预期。
- **[cancel 后台线程仍占 db_ctx 到当前节点结束]** -> `finally` `pool.release` 兜底，refcount 正常递减；不长持有。
- **[cancelled 复用 error 节点视觉]** -> 用 `rejection=false` + `cancelled=true` + summary "用户已取消" + AssistantCard 单独文案区分，reducer/inspector 按 `cancelled` 标记走分支。
- **[resume 期间取消]** 反问 awaiting_clarification 态无在途流，停止按钮应禁用；仅 `streaming`/`sending` 态可取消。-> `Conversation` 按 `sending` 控制按钮可见性。

## Migration Plan

1. **后端先行（向后兼容）**：`cache_confirm` payload 加 `kind`/`options`（旧字段保留）；`query.py` 加 `cancel_event` 与断连检测（不触发则零影响）。
2. **前端跟进**：`types`/`reducer`/`store` 加新字段与 `cancelled` 终态；`ClarificationBubble` 按 `kind` 渲染（缺 `kind` 回退 `open`）；`DetailInspector` 读 `inspectorTurnId`；`Conversation` 停止按钮。
3. **learn demo 独立**：可与上述并行，无耦合。
4. **回滚**：前端停止按钮可灰度隐藏；后端 `cancel_event` 纯增量（不 set 则不触发）；clarification `kind` 缺失前端回退 `open`。

## Open Questions

- `confirm` 的 `value` 编码用 `"yes"/"no"` 还是 `"是"/"否"`？（倾向 `"yes"/"no"` + 旧集合兜底）
- `cancelled` 是否新增 `TimelineNodeType 'cancelled'`？（倾向复用 `error` 节点 + `cancelled` 标记，省枚举改动）
- 停止按钮 UI：替换发送按钮 vs 并排？（倾向 `sending` 时发送按钮变体为"停止"，节省横向空间）

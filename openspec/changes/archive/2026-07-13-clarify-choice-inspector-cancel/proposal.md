## Why

流式问数链路存在三个相互独立但都影响可用性的缺口：

1. **复用反问是输入框，"好"匹配不上"是"。** 缓存命中确认（`cache_confirm`）下发 `ambiguities: []`，前端只剩自由输入框；后端用精确集合 `{"复用","是","yes",...}` 判定，用户输 "好"/"可以"/"ok" 一律被判为"重新生成"，复用静默失败。
2. **新轮一开，旧轮中间节点看不了。** 详情检查器硬编码读 `turns[turns.length - 1]`，点击旧轮节点虽正确写入该轮 `selectedNode`，检查器却不读它，导致上一轮的 IR/SS/CG 等中间产物在新轮开始后无法回看。
3. **请求一旦发起无法终止。** 前端 `useQueryStream.cancel()` 已存在但未接任何 UI，且 abort 后 Turn 永远停在 `streaming`（僵尸态）；后端 `graph.stream` 跑在后台线程里、无断连检测，客户端离开后整条图继续跑完，白烧 LLM token 与 DB 资源。

## What Changes

- **反问交互结构化（问题 1，方案 B）**：clarification 事件新增 `kind`（`confirm` / `choice` / `open`）与 `options` 字段；`cache_confirm` 节点下发 `kind=confirm` + 是/否选项；前端 `ClarificationBubble` 按 `kind` 渲染——`confirm` 为两个主按钮且隐藏输入框，`choice` 为按钮组 + 可选输入，`open` 为纯输入；选项点击直接提交结构化值，后端按布尔/枚举判定，彻底告别字符串集合匹配。
- **检查器跨轮查看（问题 2）**：store 新增 `inspectorTurnId`（`null` = 自动跟随最新 turn）；点击任意 turn 的节点同时把检查器 pin 到该 turn；检查器改为读 `inspectorTurnId ?? 最后一个 turn`；新增"返回最新"解除 pin。语义与现有 `selectedNode` 的自动跟随/锁定对称。
- **请求终止（问题 3）**：前端 `sending` 期间显示"停止"按钮，点击 `abort()` + 新增 `cancelTurn` 动作使 Turn 进入 `cancelled` 终态（修复僵尸 streaming，时间轴追加"已取消"节点）；后端 `query.py` 引入 `threading.Event` 取消信号，`graph.stream` 循环在节点边界检查信号、SSE 生成器检测客户端断开（`request.is_disconnected()` / `CancelledError`）并置位信号，后台线程在当前节点完成后退出。
- **教学 demo**：在 `learn/cancel-stream/` 下提供最小可运行复刻，逐步讲解 SSE 断流 + 线程合作式取消（含 5 篇 steps 文档）。

### BREAKING

- `clarification` SSE 事件 schema 扩展：新增 `kind`、`options` 字段。旧客户端忽略额外字段仍可工作，但 `cache_confirm` 场景的行为从"自由输入 + 字符串匹配"变为"二选一按钮 + 布尔判定"。
- `TurnStatus` 新增 `'cancelled'` 终态；`Turn` 新增 `inspectorTurnId` 相关 store 状态（不影响历史会话回放，`setHistoryTurns` 不参与）。

## Capabilities

### New Capabilities

- `clarification-interaction`: 结构化反问交互契约——clarification 的 `kind`/`options` payload、三态渲染规则、选项即提交语义、输入框显隐规则、后端匹配从字符串集合改为结构化判定。
- `request-cancellation`: 流式请求终止契约——前端 abort + `cancelled` Turn 终态、后端合作式取消信号（`threading.Event`）、SSE 断连检测、节点边界取消语义及其限制。

### Modified Capabilities

- `frontend-ui`: 详情检查器从"仅跟随最后轮"改为"可锁定到任意轮"（`inspectorTurnId` 提升），新增"返回最新"解除锁定。

## Impact

- **后端**：`src/graph/main_graph.py`（`cache_confirm` payload 加 `kind`/`options`）、`src/api/routes/query.py`（`cancel_event` 信号 + `run_graph` 节点边界检查 + `event_stream` 断连检测 + 取消时跳过 result/done 推送）、`src/api/schemas.py`（若有 clarification 的 Pydantic schema 同步）。
- **前端**：`frontend/src/api/types.ts`（`ClarificationEvent` 加 `kind`/`options`）、`store/types.ts`（`TurnStatus` 加 `cancelled`）、`store/useChatStore.ts`（`inspectorTurnId` + `cancelTurn`）、`store/reducer.ts`（`cancelled` 终态、cancelTurn 时间轴节点）、`components/ClarificationBubble`（按 `kind` 渲染）、`components/DetailInspector`（读 `inspectorTurnId` + 返回最新按钮）、`components/AgentTimeline`（被 pin turn 的视觉标记）、`components/Conversation`（停止按钮）、`hooks/useQueryStream`（`cancel` 接 UI + 调 `cancelTurn`）。
- **测试**：前端 Vitest（reducer `cancelled`、`inspectorTurnId` pin/释放、clarification `kind` 渲染分发）、后端 pytest（`cache_confirm` payload 结构、`cancel_event` 触发后 `run_graph` 在节点边界退出）、Playwright E2E（三场景：复用二选一、跨轮回看、请求终止）。
- **教学**：`learn/cancel-stream/` 新增（backend.py / frontend.html / README.md / 5 篇 steps / run.md）。
- **依赖**：无新增——`threading.Event`、`AbortController`、`request.is_disconnected()` 均为现有运行时能力。

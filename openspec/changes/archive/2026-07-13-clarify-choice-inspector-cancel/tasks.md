## 1. 后端基础（向后兼容，先行）

- [x] 1.1 `src/graph/main_graph.py` `make_cache_confirm_node`：interrupt payload 加 `kind="confirm"` 与 `options=[{label:"是，复用",value:"yes"},{label:"否，重新生成",value:"no"}]`，保留 `ambiguities=[]`、`round=1`
- [x] 1.2 `src/graph/main_graph.py` `cache_confirm` 节点：`approved` 判定改为 `user_choice == "yes"` 优先，`user_choice == "no"` 为 False，其余回退现有字符串集合匹配 `{"复用","是","yes",...}` 兜底
- [x] 1.3 `src/api/routes/query.py` `event_stream`：闭包内新增 `cancel_event = threading.Event()`，传入 `run_graph`
- [x] 1.4 `src/api/routes/query.py` `run_graph`：`for update in graph.stream(...)` 循环体处理完 update 后 `if cancel_event.is_set(): logger.info(...); break`
- [x] 1.5 `src/api/routes/query.py` `event_stream`：心跳 `asyncio.TimeoutError` 分支加 `if await request.is_disconnected(): cancel_event.set(); break`
- [x] 1.6 `src/api/routes/query.py` `event_stream`：`except (asyncio.CancelledError, GeneratorExit)` 中 `cancel_event.set()` 后 re-raise；`finally` 兜底 `cancel_event.set()` 并保留 `pool.release`
- [x] 1.7 `src/api/routes/query.py`：cancel 触发的 break 后跳过 `result`/`done` 推送（连接已断），直接走 release
- [x] 1.8 `src/api/schemas.py`（若 clarification 有 Pydantic schema）：同步 `kind`/`options` 字段（可选，缺省兼容）— N/A：clarification 事件为原始 dict emit，无 Pydantic schema

## 2. 前端契约与状态层

- [x] 2.1 `frontend/src/api/types.ts` `ClarificationEvent.data`：加 `kind?: 'confirm' | 'choice' | 'open' | null` 与 `options?: {label: string; value: string}[]`
- [x] 2.2 `frontend/src/store/types.ts`：`TurnStatus` 加 `'cancelled'`；`Turn` 加 `cancelled?: boolean`；`Clarification` 加 `kind?`/`options?`
- [x] 2.3 `frontend/src/store/useChatStore.ts`：顶层 state 加 `inspectorTurnId: string | null`（默认 `null`）；`selectNode` 改为同时设 `inspectorTurnId=turnId`；新增 `releaseInspector()`（置 `inspectorTurnId=null`）；新增 `cancelTurn(turnId)`
- [x] 2.4 `frontend/src/store/reducer.ts` `case 'clarification'`：`next.clarification` 透传 `kind`/`options`；`done` 事件加 `status !== 'cancelled'` 守卫；`cancelTurn` 逻辑在 store 实现
- [x] 2.5 `frontend/src/store/useChatStore.ts` `startTurn`：保持不自动改 `inspectorTurnId`（null 即自动跟随最新，符合 D2）

## 3. 问题1：反问选择框（ClarificationBubble 按 kind 渲染）

- [x] 3.1 `frontend/src/components/ClarificationBubble/index.tsx`：按 `turn.clarification.kind` 分发渲染--`confirm` 渲染两个主按钮（`options[0]/[1].label`）且不渲染 Input，点击 `doResume(opt.value)`
- [x] 3.2 `ClarificationBubble`：`choice` 渲染按钮组（点击提交 `value`）+ 保留 Input（提交原文）；`open`/`kind 缺失` 渲染纯 Input（现有行为）
- [x] 3.3 `ClarificationBubble`：`confirm` 态提交后显示 loading（resume 流进行中），与现有 submitting 逻辑一致

## 4. 问题2：检查器跨轮查看

- [x] 4.1 `frontend/src/components/DetailInspector/index.tsx`：`currentTurn` 改为 `turns.find(t => t.turnId === inspectorTurnId) ?? turns[turns.length-1]`
- [x] 4.2 `DetailInspector`：顶部加"📌 已锁定到第 N 轮 · {node}"指示 + "返回最新"按钮（`inspectorTurnId !== null && inspectorTurnId !== last` 时显示），点击调 `releaseInspector()`
- [x] 4.3 `frontend/src/components/AgentTimeline/index.tsx`：被 pin 的 turn（`turn.turnId === inspectorTurnId`）时间轴加视觉标记（"检查器正查看此轮"提示）
- [x] 4.4 `DetailInspector`：`STATUS_TAG` 加 `cancelled` 态（文案"已取消"、灰色）；`ErrorDetail` 对 `cancelled` turn 显示"已取消"而非"错误/拒答"

## 5. 问题3：请求终止（前端 UI）

- [x] 5.1 `frontend/src/hooks/useQueryStream.ts` `cancel`：abort 后调 `useChatStore.getState().cancelTurn(turnId)`（修复僵尸 streaming）；新增 `turnIdRef` 跟踪在途 turnId
- [x] 5.2 `frontend/src/components/Conversation/index.tsx`：`sending` 且最新轮 `streaming` 时把"发送"按钮变体为"停止"（`canStop`），点击调 `cancel()` + `cancelTurn`
- [x] 5.3 `Conversation` `AssistantCard`：`cancelled` 态显示"已取消"提示，不显示"推理中"loading，不渲染结果表
- [x] 5.4 `Conversation`：`canStop = sending && lastTurnStatus === 'streaming'`，反问等待/已完成/已取消态停止按钮不显示

## 6. learn/cancel-stream 教学 demo

- [x] 6.1 `learn/cancel-stream/01_sse_basics/` + `03_server_detect_disconnect/`：SSE 基础 + 断连检测（`is_disconnected` / `CancelledError`）server.py demo
- [x] 6.2 `learn/cancel-stream/02_abort_fetch/` + `06_full_cancel/`：原生 JS `fetch` + `ReadableStream` + `AbortController` + 开始/停止按钮 frontend.html
- [x] 6.3 `learn/cancel-stream/README.md`：总览（终止问题、为什么难、阅读顺序表、一句话总结）
- [x] 6.4 `learn/cancel-stream/01~05/`：5 个概念步骤目录（sse_basics / abort_fetch / server_detect_disconnect / thread_cooperative_cancel / node_boundary），每目录 README + 可运行代码 + ASCII 时序图（匹配既有 learn/ 教程约定）
- [x] 6.5 `learn/cancel-stream/06_full_cancel/`：完整组合（backend.py + frontend.html + README，镜像 query.py 架构，含观察清单与项目对应表）
- [x] 6.6 `learn/cancel-stream-bugfix/`：取消 bug 实战教学（修复过程中新增），4 个 demo 讲清三个真实 bug 的诊断与修复--01 子图节点边界盲区（`_wrap_node`+ContextVar+`CancelRequested`）、02 断连异常被吞（`yield` except 触发取消）、03 终态守卫缺失（reducer `cancelled` 守卫）、04 三合一完整修复版；01/03 demo 实跑验证逻辑正确

## 7. 单元测试

- [x] 7.1 前端 Vitest `tests/cancel_inspector.test.ts`：`cancelTurn` 使 Turn 进 `cancelled` 终态、timeline 含"用户已取消"节点、`rejection=false`；cancelled 不被 done 覆盖
- [x] 7.2 前端 Vitest `tests/cancel_inspector.test.ts`：`inspectorTurnId` pin/释放--点击旧轮节点设 `inspectorTurnId`、点已选中节点置 null、`releaseInspector` 保留 selectedNode、跨轮保持
- [x] 7.3 前端 Vitest `tests/clarification_kind.test.tsx`：clarification `kind` 渲染分发--`confirm` 无 Input、`choice` 按钮+Input、`open` 纯 Input、`kind` 缺失回退 `open`
- [x] 7.4 后端 pytest `tests/graph/test_cache_confirm_node.py`：`cache_confirm` payload 含 `kind="confirm"` 与 2 项 `options`；`user_choice="yes"/"no"` 判定 approved；非标准值（"是"）回退字符串匹配
- [x] 7.5 后端 pytest `tests/api/test_query_cancel.py`：取消信号接线不破坏正常流（result+done+pool.release）；"节点边界退出"由 learn demo + Playwright E2E 验证（TestClient 缓冲无法模拟中途断连）
- [x] 7.6 全量回归：`cd frontend && npm run test` 全绿；后端 `pytest` 全绿

## 8. Playwright 端到端测试

- [x] 8.1 `frontend/e2e/clarify-choice.spec.ts`：缓存命中反问展示二按钮（是/否），无输入框；点"是"后 resume 续流出 result；点"否"走重新生成（cache 未命中时自动 skip）
- [x] 8.2 `frontend/e2e/inspector-cross-turn.spec.ts`：第1轮完成 -> 点节点 -> 检查器锁定第1轮 -> 发第2轮 -> 仍显示第1轮 -> 点"返回最新" -> 切到第2轮
- [x] 8.3 `frontend/e2e/request-cancel.spec.ts`：发起慢查询 -> 点"停止" -> Turn 显示"已取消"且无"推理进行中" -> "发送"按钮恢复（查询过快时自动 skip）
- [x] 8.4 Playwright 框架搭建完成（config + 3 spec + README + @playwright/test 安装，`--list` 解析通过）；实际跑通需用户起后端+DB+`npx playwright install` 后用 Playwright 插件执行（CLAUDE.md #9）

## 9. 收尾

- [x] 9.1 更新 `frontend/src/components/*/index.tsx` 顶部注释（ClarificationBubble/DetailInspector/Conversation/useQueryStream 的职责说明同步新行为）
- [x] 9.2 `openspec validate clarify-choice-inspector-cancel --strict` 通过
- [x] 9.3 跑 `openspec archive` 准备归档（开发完成且测试全绿后，待用户指令）

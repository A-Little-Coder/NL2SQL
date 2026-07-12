## Why

端到端测试暴露三个体验缺口，前两个是"透明度"问题，第三个是"可操作性"问题：

1. **新会话命中缓存让人困惑**：用户在新会话提问时触发了缓存命中，但前端时间轴只显示 `缓存命中 · metric_definition · conf=0.92`--看不出命中的是哪个指标、为什么新会话也有缓存。根因是命中的是用户长期记忆里的指标定义（`user_memory.metric_definitions`，按 user_id 跨会话持久化），属预期行为，但当前 UI 既不解释来源也不暴露指标名，用户无从理解。这是长期记忆的**可解释性缺口**，不是后端逻辑 bug。

2. **三栏分隔条几乎不可见**：`resize-handle` 默认 `background: transparent` + `width: 4px`，只有鼠标恰好悬停那 4px 才变蓝。可发现性（affordance）接近零，用户找不到拖动位置。

3. **边栏展开态无一键折叠按钮**：当前 `CollapsedBar` 只在已折叠态渲染展开按钮，展开态没有任何折叠入口，用户想折叠只能手动把分隔条拖到最小阈值以下。`leftRef/rightRef` 已是 `ImperativePanelHandle`，`collapse()` 方法现成可用，缺的只是一个按钮。

## What Changes

**后端（`src/`）**：
- `HistoryCache` 命中 `source=metric_definition` 时，`CacheResult` 输出 `matched_metric_name`（命中的指标名），供前端解释"来自长期记忆·{指标名}"
- `cache_check` SSE 事件 payload 增补 `matched_metric_name` 字段（命中 session_history 时为 null）

**前端（`frontend/`）**：
- `cache_check` 事件类型扩展 `matched_metric_name`；时间轴 cache 节点摘要按 source 区分展示--`metric_definition` 命中显示"来自长期记忆·{指标名}"并支持查看指标定义，`session_history` 命中显示"来自会话历史·{历史查询}"
- `resize-handle` 改为默认可见：常驻淡色分隔线 + hover/拖拽加深 + 中部抓手 affordance
- `AppLayout` 左右边栏展开态角落补折叠按钮，调 `panel.collapse()`，与已有 `CollapsedBar`（展开按钮）对称

## Capabilities

### New Capabilities
<!-- 无新增 capability，三项均修改现有 capability -->
（无）

### Modified Capabilities
- `frontend-ui`: 缓存命中来源可视化（区分 `session_history`/`metric_definition`，展示指标名或历史查询）；三栏分隔条默认可见可拖拽；边栏展开态提供一键折叠按钮
- `api-service`: `cache_check` 事件 payload 增补 `matched_metric_name`（命中 `metric_definition` 时为指标名，其余为 null）
- `session-memory-hybrid-recall`: HistoryCache 命中 `metric_definition` 时，`CacheResult` 输出 `matched_metric_name` 供下游解释

## Impact

- **后端改动**：`src/memory/history_cache.py`（`CacheResult` 加 `matched_metric_name`、`_parse_response` 提取指标名）、`src/memory/prompts.py`（`CACHE_CHECK_PROMPT` 输出格式加 `matched_metric_name` 字段）、`src/graph/main_graph.py`（`cache_check` 事件 emit 增补字段）。命中检测行为不变，仅多输出一个解释字段，向后兼容。
- **前端改动**：`frontend/src/api/types.ts`（`CacheCheckEvent` 加 `matched_metric_name`）、`store/reducer.ts`（cache 摘要按 source 区分）、`components/AgentTimeline`（cache 节点展示指标名 + 查看入口）、`components/AppLayout.tsx` + `AppLayout.css`（折叠按钮 + resize-handle 可见性）。
- **测试**：后端 `_parse_response` 提取 `matched_metric_name` 单测、`cache_check` 事件字段单测；前端 reducer cache 摘要区分 source 单测、AgentTimeline 命中展示单测、AppLayout 折叠按钮交互测试、resize-handle 可见性样式测试。
- **契约兼容**：`cache_check` payload 仅增字段（`matched_metric_name`），旧前端忽略无害；无破坏性变更。
- **非目标**：不改 `session_recall` 的 session_id 隔离逻辑（已正确，新会话 session_history 召回为空）、不改 `cache_confirm` 反问流程、不做长期记忆的"忘记指标"管理功能、不做结果图表可视化。

## Context

`cache-hit-ux-and-layout` 的三项改动来自端到端测试暴露的体验缺口（详见 [proposal.md](./proposal.md)）：

- **长期记忆命中可解释性**：`history_cache` 节点命中 `source=metric_definition` 时，前端只显示 `缓存命中 · metric_definition · conf=...`，用户看不出命中的是哪个指标、为何新会话也有缓存。当前 `CacheResult`（`src/memory/history_cache.py`）只输出 `cached_sql`/`source`/`confidence`/`historical_query`，没有"命中的指标名"。`cache_check` SSE 事件（`src/graph/main_graph.py:187`）payload 同样缺该字段。
- **三栏分隔条不可见**：`AppLayout.css` 的 `.resize-handle` 默认 `background: transparent` + `width: 4px`，仅 hover/active 显现。
- **边栏展开态无折叠按钮**：`AppLayout.tsx` 的 `CollapsedBar` 只在已折叠态渲染展开按钮，展开态分支（`leftCollapsed === false`）无任何折叠入口。`leftRef`/`rightRef` 已是 `ImperativePanelHandle`，`collapse()` 现成可用。

现有约束：
- `session_recall` 的 `session_id` 隔离逻辑正确，**不动**（新会话 `session_history` 召回为空是预期）
- `cache_confirm` 反问流程**不动**
- `cache_check` payload 仅增字段，向后兼容

## Goals / Non-Goals

**Goals:**
- 用户能在时间轴 cache 节点上一眼看出"命中来自长期记忆的哪个指标"还是"来自本会话历史"，不再对新会话命中感到困惑
- 三栏分隔条默认可见、可拖拽，用户能找到拖动位置
- 左右边栏展开态有一键折叠按钮，与折叠态的展开按钮对称

**Non-Goals:**
- 不改 `session_recall` 隔离逻辑、不改 `cache_confirm` 反问流程、不改命中检测的 LLM prompt 判断规则
- 不做长期记忆的"忘记指标"管理功能（指标定义的增删改由 `UserMemoryView` 现有能力覆盖，本 change 不扩展）
- 不做结果图表可视化、不做生产部署

## Decisions

### D1: `matched_metric_name` 的获取--后端反查而非 LLM 返回

**选择**：后端在 `HistoryCache._parse_response` 里，当 `source=metric_definition` 时，按 `cached_sql` 在 `metric_definitions` 中反查指标名；找不到则 `matched_metric_name = None`。

**Alternatives**：
- **A. 扩展 `CACHE_CHECK_PROMPT` 让 LLM 返回 `matched_metric_name`**：拒绝。LLM 返回指标名不稳定（可能幻觉、拼错、大小写不一），而 `metric_definitions` 是确定的有限集合；让 LLM 多输出一个字段也增加 prompt 复杂度与解析失败面。
- **B. 后端反查（采纳）**：已有先例--`session_history` 的 `historical_query` 回填就是用 `cached_sql` 精确反查兜底（`history_cache.py:158-165`）。指标名反查复用同一思路，确定性强。

**反查匹配规则**：`norm(sql) = strip + rstrip(";") + strip + lower()`，对 `cached_sql` 与每个 `metric.sql_pattern` 归一化后比较，首个命中取该 metric 的 `name`。`sql_pattern` 是 `METRIC_EXTRACT_PROMPT` 提取的"简化 SQL 模式"，与 LLM 复用的 `cached_sql` 应基本一致；不一致时降级为 `None`，前端显示"长期记忆"不带名。

### D2: `resize-handle` 可见性--常驻淡色线 + 抓手点

**选择**：保持 `width: 4px`，但用 `::before` 画一条常驻淡色 1px 竖线（`#e0e0e0`），中央用 `::after` 放一个 8px 抓手点（两个小圆点）；hover/active 时竖线与抓手加深为 `#1677ff`、抓手放大。

**Alternatives**：
- **A. 单纯常驻淡色背景**：拒绝。仍缺"可拖拽"的 affordance 提示。
- **B. 加宽到 6-8px**：拒绝。浪费横向空间，边栏内容区被挤压。
- **C. 常驻线 + 抓手点（采纳）**：抓手点是拖拽的经典视觉提示（IDE、设计工具通用），4px 宽度内用伪元素绘制不侵占内容区。

### D3: 折叠按钮位置--边栏内顶部角落

**选择**：在 `AppLayout.tsx` 展开态分支（`<div className="sider-inner">` 内）顶部用绝对定位放一个折叠按钮，左栏 `MenuFoldOutlined`、右栏 `MenuUnfoldOutlined`（与 `CollapsedBar` 现有图标一致），点击调 `leftRef.current?.collapse()` / `rightRef.current?.collapse()`。

**Alternatives**：
- **A. 分隔条上嵌入折叠按钮**：拒绝。增加 `resize-handle` 复杂度，与拖拽手势冲突（点击 vs 拖拽难区分）。
- **B. 边栏内顶部角落（采纳）**：与 `CollapsedBar`（折叠态展开按钮）语义对称；IDE 风格的常见位置；不侵入 `SessionSidebar`/`DetailInspector` 组件内部（按钮由 `AppLayout` 持有，调 panel ref）。

**实现要点**：按钮 `position: absolute; top: 8px; right: 8px`（左栏）/ `left: 8px`（右栏），`sider-inner` 加 `position: relative` + 顶部 `padding-top` 避免遮挡组件首行。

### D4: cache 节点命中展示--按 source 区分文案 + 指标查看入口

**选择**：`reducer.ts` 的 cache 摘要按 `source` 渲染中文友好文案，`AgentTimeline` cache 节点：
- `source=metric_definition`：显示"长期记忆·{matched_metric_name}"（`matched_metric_name` 为 null 时仅"长期记忆"），右侧附"查看"入口，点击 popover 展示指标名与复用 SQL（`matched_metric_name`/`cached_sql`，事件已有字段，不依赖 userMemory 加载）
- `source=session_history`：显示"会话历史·{historical_query 截断}"
- 其他/null：显示"缓存命中"

**原 `source` 英文值保留在节点 `title` 属性**供调试。`matched_metric_name` 从扩展后的 `cache_check` 事件取。

**Alternatives**：
- **A. 点击跳转到 `UserMemoryView` 对应指标**：拒绝。会离开当前对话上下文，打断流程。
- **B. popover 就地展示（采纳）**：不离开对话，轻量。

### D5: 前端类型与事件契约扩展

- `frontend/src/api/types.ts` 的 `CacheCheckEvent` 增补 `matched_metric_name?: string | null`
- `store/reducer.ts` 的 `cache_check` 分支：摘要由 `缓存命中 · ${d.source} · conf=...` 改为按 D4 文案规则
- 后端 `CacheResult` dataclass 增 `matched_metric_name: Optional[str] = None`；`main_graph.py` 的 `cache_check` emit 增该字段

### D6: CollapsedBar 折叠态 fixed 定位（修复 enhance change 遗留 bug）

**选择**：CollapsedBar 容器 div 改用 `position: fixed`（top:64, width:40, height:calc(100vh-64px), z-index:1000），脱离 0 宽 PanelGroup 流，浮在视口边缘可点。同时折叠态对应 resize-handle 设 `pointer-events: none`（panel 已 0 宽，拖拽无意义）。

**背景**：7.3 端到端验证发现，`collapsedSize=0` 时 CollapsedBar 按钮在 0 宽 panel 内溢出，被相邻 PanelGroup/resize-handle 拦截，点击超时。这是 `enhance-ir-display-and-layout` 遗留的 bug（当时只在 jsdom 测 CollapsedBar 组件，未在浏览器验证折叠态展开）。

**Alternatives**：
- **A. 提高 collapsedSize**：拒绝。collapsedSize 必须 < minSize(6%)，无法容纳按钮。
- **B. 按钮 z-index**：拒绝。0 宽 panel 按钮溢出到相邻 panel，z-index 在不同 stacking context 不可靠。
- **C. fixed 定位（采纳）**：彻底脱离 PanelGroup，按钮稳定可点；视觉上浮在边缘 40px，符合"窄展开条"语义。

## Risks / Trade-offs

- **[matched_metric_name 反查不准]** -> `sql_pattern` 与 `cached_sql` 归一化后仍不匹配时降级为 `None`，前端显示"长期记忆"不带名；不影响命中复用逻辑（复用仍按 `cached_sql` 执行）。初版用 `strip + rstrip(";") + lower`，若反查率低可在后续迭代引入 LLM 返回 name 作辅助。
- **[resize-handle 抓手点遮挡内容]** -> 抓手点在 4px handle 内用伪元素绘制，不超出 handle 边界，不侵占 `sider-inner` 内容区。
- **[折叠按钮浮在内容上遮挡首行]** -> `sider-inner` 顶部加 `padding-top: 40px`（或按钮高度 + 间距），`SessionSidebar`/`DetailInspector` 首行下移避让。
- **[前端摘要文案改动影响现有 reducer 单测]** -> `reducer.ts` 的 `cache_check` 单测需同步更新断言，属预期改动。

## Migration Plan

- 纯增量改动，无数据迁移、无破坏性契约变更。
- 发布顺序：后端先（`cache_check` 增字段）-> 前端后（消费新字段）。但旧前端忽略 `matched_metric_name` 无害，可同时发布。
- **回滚**：前端回滚仍可用（`matched_metric_name` 缺失时降级显示"长期记忆"）；后端回滚则前端取到 `undefined`，同样降级。双向兼容。

## Open Questions

- **`matched_metric_name` 反查的归一化程度**：初版 `strip + rstrip(";") + lower`，是否需要忽略空白差异/别名重写？-> 留 tasks 阶段用真实指标数据验证反查命中率，不达标再调整。
- **折叠按钮是否需要独立持久化折叠态**：当前 `react-resizable-panels` 的 `autoSaveId="nl2sql-layout-v2"` 已持久化面板尺寸，折叠态通过 `size=0` 体现，无需额外存储。确认即可。

## 1. 后端：HistoryCache 输出 matched_metric_name

- [x] 1.1 `CacheResult` dataclass 增 `matched_metric_name: Optional[str] = None` 字段（`src/memory/history_cache.py`）
- [x] 1.2 `HistoryCache.check()` 把 `metric_definitions` 透传给 `_parse_response`（当前只传 `session_history`）
- [x] 1.3 `_parse_response` 在 `source=metric_definition` 命中时，按 `cached_sql` 与 `metric_definitions[].sql_pattern` 归一化反查（`norm = strip + rstrip(";") + lower`），首个匹配取该指标 `name` 填入 `matched_metric_name`；反查失败为 None
- [x] 1.4 单测：`_parse_response` 三场景--命中 metric_definition 反查到指标名 / 反查失败为 None / 命中 session_history 为 None

## 2. 后端：cache_check 事件 payload 扩展

- [x] 2.1 `main_graph.py` `make_history_cache_node` 的 `emit_safe("cache_check", {...})` 增 `matched_metric_name` 字段（取自 `result.matched_metric_name`）
- [x] 2.2 单测：`cache_check` 事件 payload 在命中 metric_definition / 命中 session_history / 未命中三种情况下 `matched_metric_name` 的正确性

## 3. 前端：类型与 reducer

- [x] 3.1 `frontend/src/api/types.ts` 的 `CacheCheckEvent` 增 `matched_metric_name?: string | null`（附带增 `historical_query?: string \| null` 供 session_history 摘要使用）
- [x] 3.2 `store/reducer.ts` 的 `cache_check` 分支：摘要按 `source` 区分--`metric_definition` -> `长期记忆·{matched_metric_name}`（null 时仅"长期记忆"），`session_history` -> `会话历史·{historical_query 截断}`，其他 -> `缓存命中`；`matched_metric_name` 存入 `turn.details.cache`
- [x] 3.3 单测：`reducer` 的 `cache_check` 三种 source 摘要断言更新（原有 `缓存命中 · {source}` 断言同步改写）

## 4. 前端：AgentTimeline cache 节点展示

- [x] 4.1 `AgentTimeline` cache 节点按新摘要渲染（"长期记忆·{name}" / "会话历史·{query}" / "缓存命中"）
- [x] 4.2 `metric_definition` 命中且 `matched_metric_name` 非空时，渲染"查看"入口，点击 popover 就地展示指标名与复用 SQL（`matched_metric_name`/`cached_sql`）
- [x] 4.3 节点 `title` 属性保留原 `source` 英文值供调试
- [x] 4.4 单测：AgentTimeline 命中展示三场景渲染断言（有指标名 + 查看入口 / 无指标名 / session_history）

## 5. 前端：三栏分隔条可见性

- [x] 5.1 `AppLayout.css` 的 `.resize-handle` 改为默认可见：`::before` 画常驻淡色 1px 竖线（`#e0e0e0`），`::after` 画中央 8px 抓手点；hover/`[data-resize-handle-active]` 时竖线与抓手加深为 `#1677ff`
- [x] 5.2 保持 `.resize-handle` `width: 4px` 不变，伪元素不超出 handle 边界、不侵占 `sider-inner` 内容区
- [x] 5.3 视觉验证：默认可见淡色竖线 + 抓手点，hover/拖拽高亮，宽度不超 4px

## 6. 前端：边栏一键折叠按钮

- [x] 6.1 `AppLayout.tsx` 展开态分支（`<div className="sider-inner">` 内）顶部角落加绝对定位折叠按钮：左栏 `MenuFoldOutlined` 调 `leftRef.current?.collapse()`，右栏 `MenuUnfoldOutlined` 调 `rightRef.current?.collapse()`
- [x] 6.2 `.sider-inner` 加 `position: relative` + 顶部 `padding`（约 40px）避让折叠按钮，不遮挡 `SessionSidebar`/`DetailInspector` 首行
- [x] 6.3 折叠按钮由 `AppLayout` 持有，不侵入 `SessionSidebar`/`DetailInspector` 组件内部
- [x] 6.4 交互测试：展开态点击折叠 -> 边栏折叠为 `CollapsedBar` -> 点击 `CollapsedBar` 展开按钮恢复展开态

## 7. 联调与端到端验证

- [x] 7.1 启动前后端，新会话提问命中 `metric_definition`，验证时间轴显示"长期记忆·{指标名}" + "查看"popover 展示指标定义
- [x] 7.2 验证三栏分隔条默认可见、hover 高亮、可拖拽调整宽度
- [x] 7.3 验证左右边栏展开态一键折叠、折叠态一键展开，双向对称（顺带修复 CollapsedBar 0 宽 panel 按钮不可点遗留 bug，见 design D6）
- [x] 7.4 回归：`cache_confirm` 反问流程端到端验证（复用 -> 执行 -> 结果）；`session_history` 命中展示与未命中走完整 IR 链路由单测覆盖（reducer + AgentTimeline + TestMatchedMetricName）

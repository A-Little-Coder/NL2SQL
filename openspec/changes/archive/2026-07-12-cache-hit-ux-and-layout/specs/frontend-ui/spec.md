## ADDED Requirements

### Requirement: 缓存命中来源可视化

时间轴 cache 节点 SHALL 按 `cache_check` 事件的 `source` 区分展示命中来源。`source=metric_definition` 时 MUST 显示"长期记忆·{matched_metric_name}"（`matched_metric_name` 为 null 时仅显示"长期记忆"），并提供查看入口就地展示指标名与复用 SQL（`matched_metric_name` / `cached_sql`，事件已有字段，不依赖 userMemory 加载状态）；`source=session_history` 时 MUST 显示"会话历史·{historical_query 截断}"。原 `source` 英文值 MUST 保留在节点 title 属性供调试。

#### Scenario: 命中长期记忆指标并展示指标名
- **WHEN** `cache_check` 事件 `hit=true` 且 `source=metric_definition` 且 `matched_metric_name` 非空
- **THEN** 时间轴 cache 节点摘要显示"长期记忆·{matched_metric_name}"
- **AND** 节点提供"查看"入口，点击后 popover 就地展示指标名与复用 SQL（`matched_metric_name` / `cached_sql`）

#### Scenario: 命中长期记忆但指标名缺失
- **WHEN** `cache_check` 事件 `hit=true` 且 `source=metric_definition` 且 `matched_metric_name` 为 null
- **THEN** 时间轴 cache 节点摘要显示"长期记忆"（不带指标名）
- **AND** 不渲染查看入口

#### Scenario: 命中会话历史
- **WHEN** `cache_check` 事件 `hit=true` 且 `source=session_history`
- **THEN** 时间轴 cache 节点摘要显示"会话历史·{historical_query 截断}"

### Requirement: 三栏分隔条默认可见

三栏布局的左右分隔条 SHALL 默认可见（常驻淡色竖线），并提供视觉抓手提示可拖拽。分隔条 hover 或拖拽时 MUST 加深高亮。分隔条宽度 MUST NOT 超过 4px 以避免侵占内容区。

#### Scenario: 分隔条默认可见
- **WHEN** 页面渲染三栏布局
- **THEN** 左右分隔条显示常驻淡色竖线
- **AND** 分隔条中部显示抓手点提示可拖拽

#### Scenario: 悬停或拖拽高亮
- **WHEN** 用户鼠标悬停或拖动分隔条
- **THEN** 分隔条竖线与抓手点加深为主题色
- **AND** 分隔条宽度保持不超过 4px

### Requirement: 边栏展开态提供一键折叠按钮

左右边栏展开态 SHALL 在边栏内顶部角落提供一键折叠按钮。点击折叠按钮 MUST 调用面板 `collapse()` 将边栏折叠为窄展开条，与折叠态的展开按钮（`CollapsedBar`）对称。折叠按钮 MUST NOT 侵入边栏内容组件内部，由布局层（`AppLayout`）持有。

#### Scenario: 展开态点击折叠
- **WHEN** 边栏处于展开态，用户点击边栏顶部角落的折叠按钮
- **THEN** 边栏折叠为窄展开条（`CollapsedBar`），原内容组件 unmount
- **AND** 折叠态窄展开条显示展开按钮

#### Scenario: 折叠按钮不遮挡内容
- **WHEN** 边栏处于展开态
- **THEN** 折叠按钮位于顶部角落，边栏内容区顶部预留 padding 避免遮挡首行

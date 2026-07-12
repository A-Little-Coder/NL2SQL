## ADDED Requirements

### Requirement: IR 节点按关键词组聚合展示字段与值召回

IR 详情检查器 SHALL 按关键词组逐组展示：每组含 phrase、同义词 terms、召回字段（带 score）、召回值（带 score）。值召回的组归属 MUST 直接使用后端 `schema_recall` 事件中 `keyword_groups[].values`（已由后端 `source_phrase` 归属），前端 MUST NOT 自行按 `table.column` 猜测归属。

#### Scenario: 多关键词组逐组渲染
- **WHEN** `schema_recall` 事件携带多个 keyword_groups
- **THEN** IR 详情按组渲染，每组一个区块，展示 phrase 标题、terms 同义词标签、召回字段列表（含 table.column 与 score）、召回值列表（含 value 与所属 table.column 与 score）

#### Scenario: 空召回组展示占位
- **WHEN** 某关键词组的 columns 与 values 均为空
- **THEN** 该组仍渲染，字段与值区域展示"无召回"占位
- **AND** 不隐藏该组

#### Scenario: 值召回准确归属不猜测
- **WHEN** 某 value 的所属列被多个关键词组召回
- **THEN** 该值仅出现在后端 `source_phrase` 指定的组下
- **AND** 前端不按 `table.column` 反查自行归属，避免多组误归

### Requirement: SS 阶段在时间轴可见

时间轴 SHALL 为 `ss`（Schema Selection）阶段渲染独立节点。`ss` 与 `schema_finalize` 的 `stage` 事件 MUST 点亮该节点，`schema_finalize` 事件 MUST 更新节点摘要（如展示 join_edges/bridge_tables）。

#### Scenario: SS 节点点亮
- **WHEN** SSE 流推送 `stage(ss, started)` 与 `stage(ss, done)`
- **THEN** 时间轴出现"Schema 选择"节点并按 started/done 切换状态
- **AND** 节点摘要展示选出的表/列数（若 stage 携带）

#### Scenario: schema_finalize 更新 SS 节点
- **WHEN** `schema_finalize` 事件到达（含 join_edges/bridge_tables）
- **THEN** SS 节点摘要更新为含 join_edges 与 bridge_tables 信息
- **AND** 检查器 SS 详情区展示 join_edges 与 bridge_tables

### Requirement: 三栏宽度可拖拽调整

三栏布局 SHALL 支持用户拖拽左右分隔条动态调整边栏宽度。左右栏 MUST 有最小宽度（折叠阈值）与最大宽度约束，中栏占剩余空间。边栏宽度 MUST 持久化到 localStorage，刷新后恢复。

#### Scenario: 拖拽调整边栏宽度
- **WHEN** 用户拖动左/右分隔条
- **THEN** 对应边栏宽度实时变化，中栏自适应占剩余空间
- **AND** 边栏宽度不超过最大约束、不低于折叠阈值

#### Scenario: 宽度持久化
- **WHEN** 用户调整边栏宽度后刷新页面
- **THEN** 边栏恢复至上次调整的宽度

### Requirement: 边栏窄于阈值时隐藏内容

当边栏宽度被拖拽至折叠阈值以下时，系统 MUST 折叠边栏：仅保留一条窄展开条（含展开按钮），边栏内容组件 MUST 完全 unmount 不渲染。系统 MUST NOT 在中间宽度态压扁边栏内容。

#### Scenario: 拖至阈值以下折叠
- **WHEN** 用户将左/右边栏拖至最小阈值以下
- **THEN** 边栏折叠为窄展开条（►/◄），原内容组件 unmount
- **AND** 不出现内容被压缩变形的中间态

#### Scenario: 点击展开条恢复
- **WHEN** 边栏处于折叠态，用户点击展开条
- **THEN** 边栏恢复至上次展开宽度，内容组件重新 mount

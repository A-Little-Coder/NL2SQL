## ADDED Requirements

### Requirement: 详情检查器跨轮锁定与自动跟随

store SHALL 新增顶层 `inspectorTurnId: string | null`（`null` = 自动跟随最新 turn）。`DetailInspector` SHALL 读取 `inspectorTurnId` 对应的 turn；`inspectorTurnId === null` 时 SHALL 回退读取最后一个 turn（自动跟随最新）。`selectNode(turnId, node)` SHALL 在设置该 turn 的 `selectedNode` 同时将 `inspectorTurnId` 置为 `turnId`（锁定到该 turn）。点击当前已选中节点 SHALL 将 `inspectorTurnId` 与该 turn 的 `selectedNode` 同置为 `null`（恢复全自动跟随）。`inspectorTurnId` 锁定到非最新 turn 时，新 turn 开始 SHALL NOT 自动切换检查器；检查器 SHALL 显示"已锁定到第 N 轮"并提供"返回最新"按钮，点击 SHALL 调用 `releaseInspector()` 置 `inspectorTurnId=null`。

#### Scenario: 默认自动跟随最新 turn
- **WHEN** `inspectorTurnId === null` 且存在多个 turn
- **THEN** 检查器显示最后一个 turn 的节点详情（自动跟随最新节点或其 `selectedNode`）
- **AND** 行为与本次改动前一致

#### Scenario: 点击旧轮节点锁定到该轮
- **WHEN** 用户点击第 1 轮（非最新）的 IR 节点
- **THEN** `inspectorTurnId` 置为第 1 轮的 turnId，第 1 轮 `selectedNode='ir'`
- **AND** 检查器切换显示第 1 轮的 IR 详情

#### Scenario: 锁定后新轮不自动切换检查器
- **WHEN** 检查器锁定在第 1 轮，用户发起新查询产生第 2 轮
- **THEN** 检查器保持显示第 1 轮详情，不跟随第 2 轮
- **AND** 检查器顶部显示"已锁定到第 1 轮"与"返回最新"按钮

#### Scenario: 返回最新解除锁定
- **WHEN** 检查器锁定在旧轮，用户点击"返回最新"按钮
- **THEN** `inspectorTurnId` 置为 `null`
- **AND** 检查器切回显示最新 turn

#### Scenario: 点击已选中节点解除锁定
- **WHEN** 用户点击当前已锁定的节点（`selectedNode` 已是该 type）
- **THEN** `inspectorTurnId=null` 且该 turn `selectedNode=null`
- **AND** 检查器恢复全自动跟随最新 turn 的最新节点

### Requirement: 拒绝复用时缓存短路不隐藏完整链路

当 `cache_confirm` 用户选择重新生成（`approved=false`）时，时间轴 SHALL 展示后续 `ir`/`ss`/`cg`/`execution`/`decision` 等完整链路节点，MUST NOT 因 `cache` 曾命中而短路隐藏。缓存命中短路（仅显示辅助节点 + cache + result）仅在用户确认复用（`approved=true`）或未触发 `cache_confirm` 时生效。

#### Scenario: 拒绝复用后 ir/cg 节点可见
- **WHEN** `cache` 命中且 `cache_confirm` `approved=false`，后端重新跑 ir/ss/cg 并推送对应 stage 事件
- **THEN** 时间轴展示 ir / cg 等节点
- **AND** 不因 `cache.hit=true` 过滤隐藏这些节点

#### Scenario: 确认复用时仍短路
- **WHEN** `cache` 命中且 `cache_confirm` `approved=true`
- **THEN** 时间轴短路展示（仅辅助节点 + cache + cache_confirm + result），不显示 ir/ss/cg

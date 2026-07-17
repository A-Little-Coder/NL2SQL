## ADDED Requirements

### Requirement: 跨会话并发不互斥

输入、发送、取消 SHALL 按会话维度独立跟踪，MUST NOT 使用跨会话共享的全局"发送中"态。某一会话存在 streaming 中的 Turn 时，MUST NOT 因此禁用其他会话的输入框或发送按钮。切换会话 MUST NOT 重置其他会话的在途态。

#### Scenario: 会话 A 流式中会话 B 可输入发送

- **WHEN** 会话 A 有一条 Turn 处于 streaming，用户切换到会话 B
- **THEN** 会话 B 的输入框可输入、发送按钮可点击
- **AND** 在会话 B 发送新查询时，会话 A 的流不受影响、继续推进

#### Scenario: 双会话并发各自完成

- **WHEN** 会话 A 流式期间在会话 B 发起查询，两会话均有在途 Turn
- **THEN** 两条 SSE 流独立完成，各自 Turn 进入 done 态并展示结果
- **AND** 一方的结果/错误 MUST NOT 串入另一会话

#### Scenario: 切换会话不重置在途态

- **WHEN** 会话 A 有 streaming Turn，用户切到 B 再切回 A
- **THEN** 会话 A 的 Turn 仍处于 streaming（或其间已完成的终态），MUST NOT 因切换被错误重置为初始态

### Requirement: 单会话内单在途

同一会话内 SHALL 同时只允许一个在途 Turn。会话已有 Turn 处于 streaming 时，该会话的发送 SHALL 被拦截（发送按钮呈现为"停止"），MUST NOT 在同一会话并发起第二条查询。

#### Scenario: 同会话流式中发送变停止

- **WHEN** 会话 A 已有 Turn 处于 streaming，用户在会话 A 再次尝试发送
- **THEN** 发送按钮呈现"停止"形态，发送被拦截
- **AND** 点击"停止"取消该会话在途 Turn，取消 MUST NOT 影响其他会话

### Requirement: 后台会话流持续更新

当某会话的 Turn 正在 streaming 但用户已切换到其他会话时，该后台会话的 SSE 事件 SHALL 持续 reduce 进该会话的 turns 缓存。用户切回该会话时 SHALL 看到已推进/已完成的 Turn 状态，MUST NOT 出现停滞在切换瞬间的时间轴。

#### Scenario: 切回后台会话见推进

- **WHEN** 会话 A 的 Turn streaming 中用户切到 B，A 后台持续收到若干 stage 事件并最终 done，用户切回 A
- **THEN** 会话 A 的 Turn 呈现已完成态（done）并展示最终结果
- **AND** 时间轴反映后台期间已发生的全部节点，MUST NOT 停滞在切走瞬间

### Requirement: 会话侧栏运行态指示

`SessionSidebar` SHALL 为存在在途 Turn 的会话展示运行态指示（如 spinner/角标）；该 Turn 完成（done/error/cancelled）时 SHALL 清除指示。

#### Scenario: 在途会话显示运行指示

- **WHEN** 会话 A 有 streaming Turn
- **THEN** 侧栏会话 A 项展示运行态指示
- **AND** 会话 A Turn 终态后指示清除

### Requirement: 多流取消按 turnId 隔离

取消逻辑 SHALL 按 `turnId` 跟踪各自在途流的 `AbortController`（Map 结构），取消某 Turn MUST 仅 abort 其对应的 fetch，MUST NOT 误伤其他在途流。

#### Scenario: 取消会话 A 不影响会话 B

- **WHEN** 会话 A、B 各有在途 Turn，用户取消会话 A 的 Turn
- **THEN** 仅会话 A 的 fetch 被 abort、Turn 进入 cancelled 终态
- **AND** 会话 B 的流继续推进不受影响

## ADDED Requirements

### Requirement: NL 问句改写
系统 SHALL 在 history_cache 之前提供一个独立的 Rewrite 环节，检测用户查询中的指代、歧义、对象缺失，利用前 5 轮会话历史改写为完整语义 query，使下游节点（history_cache / task_decomposer / ir）获得自足的查询文本。

#### Scenario: 无问题直接放行
- **WHEN** 用户查询语义完整、无指代、无歧义、无缺失
- **THEN** Rewrite 节点 SHALL 直接放行，`user_query` 保持不变，`rewritten_query` 为空字符串

#### Scenario: 指代消解改写
- **WHEN** 会话历史为 `[{"role": "user", "content": "查询苹果的销售额"}]` 且当前查询为 `"那去年的呢"`
- **THEN** Rewrite 节点 SHALL 将 `user_query` 改写为 `"查询苹果去年的销售额"`，`rewritten_query` 记录新值，`rewrite_round` 记为 1

#### Scenario: 歧义改写
- **WHEN** 查询中存在多义实体（如"苹果"可指公司或水果），且历史上下文可区分
- **THEN** Rewrite 节点 SHALL 根据上下文补全消除歧义，产出改写后的 `user_query`

#### Scenario: 对象缺失改写
- **WHEN** 查询缺少关键限定（如"查销售额"缺公司/时间），且历史上下文可补全
- **THEN** Rewrite 节点 SHALL 根据历史补全缺失限定，产出改写后的 `user_query`

#### Scenario: 最多改写 2 次
- **WHEN** 第 1 次改写后仍存在指代/歧义/缺失
- **THEN** Rewrite 节点 SHALL 进行第 2 次改写（`rewrite_round=2`）
- **AND** 第 2 次改写后仍有问题则进入拒答路径

#### Scenario: 改写失败拒答
- **WHEN** 用户查询有指代/歧义/缺失，但历史上下文不足以补全（无历史、或历史也无相关信息），或第 2 次改写后仍有问题
- **THEN** Rewrite 节点 SHALL 设置 `rejection_reason` 为"您的查询存在歧义/信息不完整，请在下一次对话中解释清楚"
- **AND** 该轮次 SHALL 写入会话历史，使用户可在下一轮继续提问

#### Scenario: 改写信息透传前端
- **WHEN** Rewrite 节点执行了改写（`rewritten_query` 非空）
- **THEN** SSE 事件 SHALL 包含 `rewritten_query` 和 `rewrite_reason` 字段，前端可展示"已为您改写为：xxx"

#### Scenario: 改写前后 query 用于 cache 匹配
- **WHEN** Rewrite 节点改写后放行
- **THEN** 改写后的 `user_query` SHALL 进入 history_cache 做命中检测，而非原始用户输入
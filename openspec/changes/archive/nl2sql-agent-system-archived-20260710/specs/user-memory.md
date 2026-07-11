## ADDED Requirements

#### Scenario: 基于 user_id 创建并加载用户记忆
给定一个新用户首次访问系统，提供 `user_id="alice_001"`
当 `UserMemory` 实例被构造并调用 `.load()` 时
则应在 `data/user_memory/alice_001.json` 创建初始记忆文件
并且文件应包含 `user_id`、`created_at`、空的 `term_preferences`、空的 `clarification_history`、空的 `frequently_used_tables`、空的 `metric_definitions`、空的 `query_preferences`、空的 `domain_context`
并且后续 `.load()` 调用应返回已存在的记忆而不覆盖

#### Scenario: 记录与查询术语偏好
给定一个用户曾澄清"销售额"指代 `gmv`
当调用 `record_term_preference("销售额", "gmv", confidence=0.9)` 后再调用 `get_term_preference("销售额")` 时
则应返回 `{"resolved_to": "gmv", "confidence": 0.9, "source": "user_taught", "last_used": <today>}`
并且 `updated_at` 字段应被刷新

#### Scenario: 追加澄清历史
给定一次完整的反问对话（原始查询、触发类型、问题、用户回答、解析结果）
当调用 `append_clarification(entry)` 时
则该条目应被追加到 `clarification_history` 列表末尾
并且条目应包含 ISO 格式的 `timestamp`

#### Scenario: 原子写入防止文件损坏
给定一个用户记忆正在被写入
当写入过程中系统异常中断时
则原 `{user_id}.json` 应保持完整未被破坏
并且临时文件 `{user_id}.json.tmp` 应可被检测到并清理

#### Scenario: 跨平台文件锁防止并发污染
给定两个进程同时尝试更新同一用户的记忆
当两个 `UserMemory.save()` 同时调用时
则一个应等待另一个完成后再写入
并且最终文件应包含两次更新的内容（无丢失）
并且应在 Windows 与 Unix 平台上均可工作（使用 msvcrt / fcntl）

#### Scenario: 缺失记忆文件的优雅降级
给定 `data/user_memory/` 目录不存在或文件丢失
当 `UserMemory.load()` 被调用时
则应自动创建目录与初始文件而非抛出异常
并且返回结构完整的空记忆字典

#### Scenario: 隐私默认明文（本期约束）
给定本期版本不处理隐私问题
当用户记忆被持久化时
则内容以明文 JSON 存储
并且 `term_preferences` 与 `clarification_history` 不做任何脱敏
并且代码注释中应明确标注「待生产化时补充隐私层」

#### Scenario: 自动学习常用表
给定用户 3 次查询均涉及 `sales` 表
当 `MemoryUpdater` 从执行结果中提取表名并调用 `record_table_usage("sales")` 时
则 `frequently_used_tables["sales"].query_count` 应递增
并且 `last_used` 应更新为当前时间

#### Scenario: 获取常用表列表（用于 IR 加权）
给定用户记忆中 `frequently_used_tables` 包含 `sales: 23次`、`orders: 15次`、`products: 8次`
当调用 `get_frequently_used_tables(top_k=2)` 时
则应返回 `["sales", "orders"]`（按 query_count 降序）

#### Scenario: 自动学习指标定义（auto_learned）
给定用户执行了 SQL `SELECT SUM(order_amount) FROM sales WHERE status='completed'`
当 `MemoryUpdater` 检测到这是一个简单聚合 SQL 并调用 `record_metric_definition("GMV", ...)` 时
则应在 `metric_definitions` 中创建条目，`source="auto_learned"`，`confidence=0.5`
并且多次使用相同 SQL 模式时 `confidence` 应递增（每次 +0.1，上限 0.9）

#### Scenario: 用户主动教指标定义（user_taught）
给定用户在反问中澄清"GMV 算的是完成订单金额"
当调用 `record_metric_definition("GMV", description="完成订单金额总和", sql_pattern="...", source="user_taught")` 时
则应覆盖已有的 auto_learned 条目，`confidence=0.95`，`source="user_taught"`
并且用户教的指标不应被自动学习覆盖

#### Scenario: 获取指标定义列表（用于 CG 注入和历史命中检测）
给定用户记忆中包含多个指标定义
当调用 `get_metric_definitions(min_confidence=0.7)` 时
则应返回 confidence >= 0.7 的指标列表

#### Scenario: 自动学习查询偏好
给定用户近 10 次查询中 8 次使用"近30天"时间范围
当 `MemoryUpdater` 统计偏好并调用 `update_query_preference("default_time_range", "last_30_days")` 时
则 `query_preferences.default_time_range` 应设为 `"last_30_days"`

#### Scenario: 获取查询偏好（用于 CG 注入）
给定用户记忆中包含查询偏好
当调用 `get_query_preferences()` 时
则应返回完整的偏好字典

#### Scenario: 更新领域上下文
给定用户长期查询销售相关数据
当调用 `update_domain_context(industry="生鲜电商", department="运营部", focus_areas=["销售分析"])` 时
则 `domain_context` 应被更新
并且 `get_domain_context()` 应返回完整领域信息

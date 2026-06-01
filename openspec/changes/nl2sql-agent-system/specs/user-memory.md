## ADDED Requirements

#### Scenario: 基于 user_id 创建并加载用户记忆
给定一个新用户首次访问系统，提供 `user_id="alice_001"`
当 `UserMemory` 实例被构造并调用 `.load()` 时
则应在 `data/user_memory/alice_001.json` 创建初始记忆文件
并且文件应包含 `user_id`、`created_at`、空的 `term_preferences`、空的 `clarification_history`
并且后续 `.load()` 调用应返回已存在的记忆而不覆盖

#### Scenario: 双轨用户身份（系统用户 + 业务角色）
给定一个登录用户 `user_id="session_abc"` 同时具备业务角色 `role_tag="sales_manager"`
当 `UserMemory(user_id="session_abc", role_tag="sales_manager")` 被实例化时
则应使用 `data/user_memory/session_abc__sales_manager.json` 作为存储路径
并且与无 `role_tag` 的同 `user_id` 记忆相互独立、互不污染

#### Scenario: 记录与查询术语偏好
给定一个用户曾澄清"销售额"指代 `gmv`
当调用 `record_term_preference("销售额", "gmv", confidence=0.9)` 后再调用 `get_term_preference("销售额")` 时
则应返回 `{"resolved_to": "gmv", "confidence": 0.9, "last_used": <today>}`
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

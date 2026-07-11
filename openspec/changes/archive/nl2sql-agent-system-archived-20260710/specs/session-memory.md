## ADDED Requirements

#### Scenario: 创建新会话
给定一个用户 `user_id="alice"` 首次发问
当调用 `SessionManager.create_session(user_id="alice")` 时
则应生成 UUID 作为 `session_id`
并且在 `data/sessions/alice/{session_id}.json` 创建初始会话文件
并且返回 `SessionMemory` 实例

#### Scenario: 加载已有会话
给定用户 `alice` 已有会话 `uuid-aaa`
当调用 `SessionManager.get_session(session_id="uuid-aaa", user_id="alice")` 时
则应从持久化文件加载会话数据
并且返回包含完整 `conversation_history` 的 `SessionMemory` 实例

#### Scenario: 会话按用户隔离
给定用户 `alice` 有会话 `uuid-aaa`，用户 `bob` 有会话 `uuid-bbb`
当查询 `alice` 的会话列表时
则不应包含 `bob` 的任何会话
并且 `bob` 尝试访问 `uuid-aaa` 应返回空或错误

#### Scenario: 一个用户可有多个会话
给定用户 `alice` 已有 3 个会话
当调用 `SessionManager.list_sessions(user_id="alice")` 时
则应返回 3 个会话的摘要（session_id、created_at、updated_at、status、turn_count）
并且按 `updated_at` 降序排列

#### Scenario: 追加对话轮次
给定会话 `uuid-aaa` 当前有 2 轮对话
当调用 `session_memory.add_turn({...})` 时
则 `conversation_history` 应追加第 3 轮
并且 `turn_index` 应为 3
并且 `updated_at` 应刷新
并且变更应持久化到 JSON 文件

#### Scenario: 获取最近 N 轮对话（用于 Prompt 注入）
给定会话 `uuid-aaa` 有 10 轮对话
当调用 `session_memory.get_recent_turns(n=3)` 时
则应返回最近 3 轮的完整对话数据（turn_index 8, 9, 10）

#### Scenario: 格式化为 Prompt 文本
给定会话 `uuid-aaa` 有 2 轮对话
当调用 `session_memory.format_for_prompt()` 时
则应返回格式化的文本，包含每轮的用户查询、生成的 SQL 和结果摘要
并且格式应适合直接插入 LLM Prompt

#### Scenario: 更新上下文摘要
给定用户上一轮查询了"苹果2025年销售额"
当本轮执行完成后 `context_summary` 被更新时
则应包含 `last_topic="苹果销售额"`、`last_tables=["sales"]`、`last_time_range="2025"`

#### Scenario: 删除会话
给定用户 `alice` 有会话 `uuid-aaa`
当调用 `SessionManager.delete_session(session_id="uuid-aaa", user_id="alice")` 时
则应删除 `data/sessions/alice/uuid-aaa.json`
并且从内存缓存中移除
并且该 session_id 后续不可再加载

#### Scenario: 内存 LRU 缓存加速
给定会话 `uuid-aaa` 已被加载到内存缓存中
当再次调用 `SessionManager.get_session(session_id="uuid-aaa")` 时
则应直接从内存返回，不读取磁盘
并且缓存容量超过上限（默认 200）时淘汰最久未访问的会话

#### Scenario: 持久化保证
给定会话在内存中被修改（追加 Turn）
当 `add_turn()` 完成后
则变更应已写入磁盘（实时持久化，非延迟写入）
并且文件写入应使用原子操作（tmp → rename）

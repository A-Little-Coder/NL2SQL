## ADDED Requirements

#### Scenario: 启动问数服务
给定所有依赖组件（DatabaseConnector、LSHIndexer、VectorStoreManager、LLMClient、各 Agent）已初始化
当 FastAPI 应用启动时
则应完成组件的一次性加载和主图构建
并且 `/api/v1/health` 应返回 `{"status": "ok"}`

#### Scenario: 发起查询请求（SSE 流式）
给定用户 `alice` 在会话 `uuid-aaa` 中发起查询"查一下苹果的销售额"
当发送 `POST /api/v1/query` 请求时
则响应应为 `text/event-stream` 格式
并且每个阶段状态变更应推送 SSE 事件（type: "stage"）
并且最终结果应推送 SSE 事件（type: "result"）
并且最后应推送完成事件（type: "done"）

#### Scenario: 查询请求的必填字段
给定一个查询请求
当请求缺少 `query` 或 `session_id` 或 `user_id` 时
则应返回 422 校验错误
并且错误信息应明确指出缺少哪个字段

#### Scenario: 新会话自动创建
给定用户 `alice` 发送请求时 `session_id` 不存在于系统中
当 `POST /api/v1/query` 处理请求时
则应自动创建新会话
并且后续使用相同 `session_id` 应能继续该会话

#### Scenario: 历史命中时的 SSE 响应
给定用户查询与历史查询等价（LLM 判断 confidence >= 0.8）
当 `POST /api/v1/query` 处理请求时
则应首先推送 `{"type": "cache_check", "hit": true, "source": "...", "confidence": 0.95}`
然后推送 `{"type": "result", "final_sql": "...", "from_cache": true}`
然后推送 `{"type": "done"}`
并且总响应时间应远小于完整链路（跳过 IR/SS/CG）

#### Scenario: 中间阶段 SSE 事件
给定查询走完整链路
当 IR 阶段开始时
则应推送 `{"type": "stage", "stage": "ir", "status": "started"}`
当 IR 阶段完成时
则应推送 `{"type": "stage", "stage": "ir", "status": "completed", "keywords": [...]}`
并且 SS、CG、Execution 阶段同理

#### Scenario: 反问时的 SSE 事件
给定 IR 检测到语义不匹配触发反问
当反问生成后
则应推送 `{"type": "clarification", "question": "...", "options": [...], "trigger_type": "..."}`
并且客户端应能通过后续请求回答反问

#### Scenario: 查询错误时的 SSE 事件
给定查询执行过程中发生错误
当错误被捕获时
则应推送 `{"type": "error", "message": "错误描述"}`
然后推送 `{"type": "done"}`

#### Scenario: 列出用户会话
给定用户 `alice` 有 3 个会话
当发送 `GET /api/v1/sessions/alice` 时
则应返回会话列表，每个包含 session_id、created_at、updated_at、status、turn_count
并且按 updated_at 降序排列

#### Scenario: 获取会话对话历史
给定会话 `uuid-aaa` 有 5 轮对话
当发送 `GET /api/v1/sessions/uuid-aaa/history` 时
则应返回完整的 5 轮对话历史
并且每轮包含 user_query、final_sql、final_result_sample、timestamp

#### Scenario: 删除会话
给定会话 `uuid-aaa` 存在
当发送 `DELETE /api/v1/sessions/uuid-aaa` 时
则应删除会话的持久化文件
并且从内存缓存中移除
并且后续访问该 session_id 应返回 404

#### Scenario: 获取用户长期记忆
给定用户 `alice` 有长期记忆
当发送 `GET /api/v1/users/alice/memory` 时
则应返回完整的用户记忆 JSON
包括 term_preferences、frequently_used_tables、metric_definitions、query_preferences、domain_context

#### Scenario: 获取用户指标定义
给定用户 `alice` 有 3 个指标定义
当发送 `GET /api/v1/users/alice/metrics` 时
则应返回指标定义列表
并且每个指标包含 name、description、sql_pattern、source、confidence

#### Scenario: API 服务并发处理
给定两个用户同时发送查询请求
当两个请求并发到达时
则应能并行处理（不阻塞）
并且各自的会话和结果互不干扰

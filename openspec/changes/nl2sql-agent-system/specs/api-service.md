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

#### Scenario: 查询请求携带 db_id（决策 49）
给定用户 `alice` 发起查询，body 中包含 `db_id: "california_schools"`
当 `POST /api/v1/query` 处理请求时
则应通过 `DbContextPool.acquire("california_schools")` 取得对应 DbContext
并且使用该 DbContext 的主图执行查询
并且查询完成后自动 release（refcount -= 1）

#### Scenario: 缺少 db_id 字段返回 422
给定一个查询请求 body 中没有 `db_id` 字段
当 `POST /api/v1/query` 处理请求时
则应返回 422 校验错误
并且错误信息应明确指出缺少 `db_id`

#### Scenario: 首次访问冷数据库懒加载（决策 49）
给定 `DbContextPool` 当前未缓存 `db_id="financial"`
当 `POST /api/v1/query` 请求 db_id=financial 时
则应触发 DbContext 构造（加载 LSH 索引 + 建主图）
并且首次响应时间可能 5-10 秒
并且后续相同 db_id 的请求应命中缓存毫秒级返回

#### Scenario: DbContext LRU 淘汰（决策 49）
给定 `DB_POOL_MAX_SIZE=2`，池中已缓存 [A, B]
当请求新的 db_id=C 到达时
则应淘汰最久未使用且 refcount=0 的 DbContext（如 A）
并且调用 A.connector.disconnect() 释放 sqlite 连接
并且构造 DbContext(C) 加入池末尾
并且池的当前状态变为 [B, C]

#### Scenario: 引用计数防止活跃 ctx 被淘汰（决策 49）
给定池满 [A, B]，且 A 正在被一个长时间查询使用（refcount=1）
当请求 db_id=C 到达时
则应跳过 A（refcount>0）寻找下一个可淘汰 ctx
并且若 B 可淘汰则淘汰 B
并且若 B 也在用则允许池短暂超 max（保留 [A, B, C]）
并且不阻塞当前请求

#### Scenario: 列出所有可用数据库（决策 49）
给定 `data/` 目录下存在多个数据库子目录
当发送 `GET /api/v1/databases` 时
则应返回所有可用 db_id 的列表
并且每项包含 `db_id` 和 `db_path`

#### Scenario: 获取指定数据库的表清单（决策 49）
给定数据库 `california_schools` 存在
当发送 `GET /api/v1/databases/california_schools/tables` 时
则应通过 DbContextPool 加载该 db 并返回表名列表
并且 finally 块中调用 pool.release

#### Scenario: 显式创建新会话（决策 49）
给定用户 `alice` 想开启新会话
当发送 `POST /api/v1/sessions` body `{"user_id": "alice"}` 时
则应通过 `session_manager.create_session("alice")` 创建会话
并且返回 `{"session_id": "<uuid>"}`
并且后续 query 使用此 session_id 可继续会话

#### Scenario: 全局单例组件不参与 LRU 淘汰（决策 49）
给定服务启动完成
当 DbContext LRU 发生淘汰时
则 BGE-M3 / VectorStore / LLMClient / SQLGenerator / SelfConsistencyDecision / AnswerabilityChecker / HistoryCache / MemoryUpdater 等全局单例不应受影响
并且这些组件在整个进程生命周期内只加载一次

#### Scenario: 启动时可选 warm-up（决策 49）
给定 `run_api.py --db_id california_schools` 启动
当 lifespan startup 完成时
则应预加载 `california_schools` 的 DbContext
并且首个 db_id=california_schools 的请求立即命中缓存
并且其它 db_id 仍按需懒加载

#### Scenario: 服务关闭释放所有 DbContext（决策 49）
给定服务收到 shutdown 信号
当 lifespan shutdown 钩子执行时
则应调用 `pool.close_all()`
并且所有缓存的 DbContext 的 sqlite 连接被关闭

#### Scenario: SSE 真流式 — 每个节点完成时立即推送（决策 50）
给定 `/api/v1/query` 收到查询请求
当 LangGraph 主图开始执行时
则每个节点（history_cache / ir / ss / answerability_check / cg / execution / decision）完成的瞬间应立即推送 SSE 事件
并且首字节响应时间应小于 5 秒（不再是"先攒后吐"）
并且事件按时间顺序到达，不需要等整条 query 完成

#### Scenario: qwen3 思考链流式推送（决策 50）
给定 LLMClient 使用 qwen3 系列模型，`enable_thinking=true`，且 `current_emitter` contextvar 已设置
当任意节点（IR / SS / Answerability / CG / Decision / ResultVerifier）调用 LLM 时
则每个 `reasoning_content` chunk 应通过 `{"type": "llm_thinking", "data": {"node": "<节点名>", "text": "<思考片段>"}}` 实时推送
并且节点名通过 `current_node` contextvar 自动注入
并且非 qwen3 模型或未开启思考时不应推送此类事件（自动降级，不报错）

#### Scenario: 不推送 LLM 正文 token（决策 50）
给定 LLMClient 流式调用 LLM，正文为 JSON 模式输出
当 OpenAI SDK 返回 `delta.content` chunk（JSON 片段如 `{`、`"answerable"`、`:` 等）时
则这些 chunk 应仅在服务端累积到 full_text，不推送任何 SSE 事件
并且 SSE 流中**不应出现** `llm_chunk` 事件类型
并且正文完整返回后由对应节点解析成结构化事件（如 `keywords` / `answerability` / `sql_candidates`）一次性推送

#### Scenario: SSE 心跳防止客户端超时（决策 50）
给定客户端 SSE 连接保持中，节点正在长时间执行 LLM 调用
当 15 秒内没有任何业务事件需要推送时
则服务应自动 yield SSE 注释行 `: heartbeat\n\n`
并且客户端读超时计时器被重置
并且反向代理（如 nginx）不会因 idle 断开连接

#### Scenario: 节点产物结构化事件（决策 50）
给定查询处于 IR 节点执行中
当关键词提取完成时
则应推送 `{"type": "keywords", "data": {"groups": [{"name": "学校", "expansions": [...]}, ...]}}`
当 schema 召回完成时
则应推送 `{"type": "schema_recall", "data": {"groups": [{"name": "学校", "top_columns": [...]}]}}`
并且 SS / Answerability / CG / Execution / Decision 节点同理推送各自的产物事件

#### Scenario: 节点执行异常透传（决策 50）
给定查询执行中某节点（如 CG）抛出异常
当异常被捕获时
则应推送 `{"type": "error", "data": {"node": "cg", "error": "<异常信息>"}}`
并且最后推送 `{"type": "done", "data": {"has_result": false}}`
并且 HTTP 状态码保持 200（不报 500，因为流已经开始）

#### Scenario: LLMClient 向后兼容（无 emitter 时退化为阻塞调用）（决策 50）
给定调用方未设置 `current_emitter` contextvar（如 CLI 脚本、单元测试、离线批处理）
当 `llm_client.chat_json(messages)` 被调用时
则应走旧的阻塞实现路径（一次性 `response.choices[0].message.content`）
并且不应抛出 ContextVar 相关异常
并且返回值与改造前一致（dict 类型）

#### Scenario: contextvar 跨线程传递（决策 50）
给定 SSE handler 在 async loop 中创建 emitter，并通过 `contextvars.copy_context().run(...)` 提交到线程池执行 graph
当 graph 内同步代码读取 `current_emitter.get()` 时
则应能正确取得 handler 设置的 emitter 实例
并且 LLMClient 的流式回调能成功调用 `emitter.emit(...)`
并且事件最终到达 asyncio.Queue

#### Scenario: 每次查询请求生成 query_id（请求级追踪）
给定客户端发起 `POST /api/v1/query` 请求
当 `query_endpoint` 进入处理时
则应在入口第一时间生成 `query_id = uuid4().hex[:12]`（12 位短 hex）
并且应输出入口日志 `[query_id={query_id}] 请求进入: user={user_id} session={session_id} db={db_id} query={query[:100]!r}`
并且应将 `query_id` 写入 `initial_state["query_id"]`
并且 `NL2SQLState` TypedDict 应声明 `query_id: str` 字段
并且两个并发请求的 `query_id` 互不相同

#### Scenario: 所有 SSE 事件 payload 都包含 query_id
给定一次查询请求生成了 `query_id="a3f8b2c91d04"`
当服务推送任意 SSE 事件（`stage` / `cache_check` / `keywords` / `schema_recall` / `answerability` / `sql_candidates` / `execution` / `final_decision` / `result` / `error` / `llm_thinking` / `done`）时
则该事件 payload 应包含 `"query_id": "a3f8b2c91d04"` 字段
并且前端可按 `query_id` 分组渲染、定位上下文
并且 `done` 事件应额外回带 `query_id` 让客户端最终确认

#### Scenario: 查询出口与异常日志带 query_id
给定一次查询请求处理完毕或抛出异常
当 `query_endpoint` 推送 `done` 事件或捕获异常时
则出口日志应输出 `[query_id={query_id}] 完成: has_result=... fix_failed=...`
并且若发生异常，`logger.exception(f"[query_id={query_id}] graph.stream 异常")` 记录完整堆栈

#### Scenario: 节点执行日志通过 query_id 串联
给定 `main_graph._wrap_node` 装饰器包裹的任意节点正在执行
当节点 enter / exit / 异常时
则装饰器应输出形如 `[qid={query_id}] [stage] node={node_name} status=started`（或 `done` / 错误信息）的日志
并且业务节点（IR / SS / CG / Decision / SmartFix 等）的关键内部日志应通过 `state.get("query_id", "")` 主动取出 query_id 拼接到日志前缀
并且通过查询日志中的 `qid=<12位 hex>` 关键字可定位某次请求所有节点的执行链路



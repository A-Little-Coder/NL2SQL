## ADDED Requirements

#### Scenario: 通过环境变量启用 LangSmith 自动追踪（路径 A）
给定环境变量 `LANGCHAIN_TRACING_V2=true` 且 `LANGCHAIN_API_KEY` 非空，`LANGCHAIN_PROJECT=NL2SQL`
当系统启动并加载 `.env` 时
则启动日志应输出形如 `LangSmith tracing enabled: project=NL2SQL` 的提示
并且业务代码不应创建任何自定义 `LangSmithMonitor` / 包装类实例
并且 LangGraph 主图与子图的节点会自动产生嵌套 trace span
并且每次 `ChatOpenAI.invoke / stream / ainvoke / astream` 调用会自动作为子 span 上报到 LangSmith
并且 `src/monitor/` 目录不应存在（已随路径 A 落地清理）

#### Scenario: 图与 LLM 调用的 run_name 命名规范
给定 LangSmith 自动追踪已启用
当一次完整的查询经过主图、子图、LLM 调用各层时
则 LangSmith UI 上的 trace 顶层应显示 `nl2sql-pipeline`（主图 run_name）
并且子图层应显示 `ir-graph` / `ss-graph` / `cg-graph` / `execution-graph` / `decision-graph`
并且 LLM 调用层应根据业务用途显示对应 run_name（如 `cache-check` / `ir-keywords` / `ir-synonyms` / `answer-check` / `ss-relevance` / `cg-generate` / `exec-smartfix` / `decision-r1` / `decision-r2` / `join-inference`）
并且 `LLMClient` 的 `invoke / stream / ainvoke / astream` 方法接受可选的 `run_name` 参数，业务侧在调用时显式传入
并且 LLMClient 内部通过 `self._chat_model.with_config(run_name=...).bind(**kw)` 实现，与 `bind` 的 model 参数互不影响

#### Scenario: 单次请求在 LangSmith 中通过 query_id 区分
给定一次 `POST /api/v1/query` 请求被处理
当 API 层执行 `graph.stream(state, config=config)` 时
则 `config` 应包含 `run_name=f"query-{query_id}"`
并且 `config.metadata` 应包含 `query_id` / `user_id` / `session_id` / `db_id` / `user_query`（截断至 200 字符）
并且 `config.tags` 应包含 `db_id` / `"api"` / `f"user:{user_id}"`
并且 `config.configurable.thread_id` 应等于 `session_id`，使 LangSmith UI 按 thread 聚合多轮会话
并且在 LangSmith UI 上可通过 `metadata.query_id="<12位 hex>"` 精确定位单次请求的完整 trace

#### Scenario: 全流程 LangSmith 追踪
当用户发起一个自然语言查询请求时
当系统经过预处理、检索、schema 选择、SQL 生成和执行各阶段
则 LangSmith 应通过 LangGraph `StateGraph` 节点的自动 span 记录每个阶段的开始和结束时间
并且它应该捕获每个阶段的输入和输出
并且它应该提供完整的 trace 链路用于调试和分析

#### Scenario: Terminal 流式输出执行过程
当系统处理用户查询时
则它应该实时向 Terminal 输出当前执行的阶段
并且它应该显示模型思考过程和推理依据
并且它应该在每个阶段完成后更新状态指示器

#### Scenario: 错误和异常的可观测性
当某个模块执行失败时
则它应该在 LangSmith 中标记错误和异常
并且它应该记录详细的错误信息和堆栈跟踪
并且它应该在 Terminal 中以醒目的方式显示错误

#### Scenario: 性能指标收集和展示
当系统完成一次查询处理后
则它应该统计并展示各阶段的耗时
并且它应该在 LangSmith 中记录性能数据用于趋势分析
并且它应该在终端输出总的处理时间和 SQL 执行结果
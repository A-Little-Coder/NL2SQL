## ADDED Requirements

> 本 spec 经 2026-06-29 重大重新定义。原方案（IR 之后基于召回结果触发四类反问 + Tavily 联网）已废弃，
> 改为「IR 之前的前置意图理解（TaskPlanner）+ interrupt 暂停恢复 + 多意图分解 + 结果总结」方案。
> 原方案的 WebSearch/Tavily（§10）、IR 后 TriggerDetector（§11）本期跳过，不实现。
>
> 新方案三选一裁决：EXECUTE（清晰→分解子查询执行）/ CLARIFY（歧义→interrupt 反问）/ REJECT（拒答）。
> 反问采用 LangGraph 1.x 的 `interrupt()` + `Command(resume=...)` + `InMemorySaver` checkpointer。

### Scenario: TaskPlanner 对清晰单意图查询直接执行

给定一个用户查询 "查询 california_schools 库中洛杉矶的公立学校数量"
当 `TaskPlanner.plan()` 被调用时
则应返回 `PlanResult(verdict="execute", intent_type="single", subqueries=["查询洛杉矶的公立学校数量"])`
并且 `subqueries` 长度应为 1
并且主流程应直接进入单查询执行路径（ir → ss → ... → decision）

### Scenario: TaskPlanner 对清晰多意图查询分解为子查询

给定一个用户查询 "查一下苹果公司的销售额和利润，再对比一下去年的"
当 `TaskPlanner.plan()` 被调用时
则应返回 `PlanResult(verdict="execute", intent_type="multi")`
并且 `subqueries` 应被分解为至少 2 个独立子查询（如 ["查苹果公司的销售额","查苹果公司的利润","对比上述指标与去年同期"]）
并且每个子查询应可独立走完整 NL2SQL 链路
并且分解结果应保持语义完整（不把单个完整意图拆碎）

### Scenario: TaskPlanner 对实体多义触发反问

给定一个用户查询 "查一下苹果的销售额"（"苹果"可能指公司或农产品，且当前 db 不确定）
当 `TaskPlanner.plan()` 被调用时
则应返回 `PlanResult(verdict="clarify")`
并且 `ambiguities` 应标注歧义实体及其候选解释（如 `{"entity":"苹果","candidates":["Apple Inc. 公司","苹果 农产品"]}`）
并且 `clarify_question` 应生成针对该歧义的澄清问题（如"您说的'苹果'是指公司还是水果？"）
并且 `clarify_round` 应反映当前已反问轮次

### Scenario: TaskPlanner 对表述不清晰触发反问

给定一个用户查询 "最近的数据"（缺失度量、维度、时间定义）
当 `TaskPlanner.plan()` 被调用时
则应返回 `PlanResult(verdict="clarify")`
并且 `clarify_question` 应指出缺失的关键维度并请用户补充
并且不应进入执行路径

### Scenario: TaskPlanner 对越权写操作拒答

给定一个用户查询包含 "删除" / "更新" / "插入" / "DROP" 等写操作意图
当 `TaskPlanner.plan()` 被调用时
则应返回 `PlanResult(verdict="reject")`
并且 `reject_reason` 应说明"本服务仅支持查询，不支持数据写操作"
并且主流程应直接 END 并返回拒答原因
并且不应执行任何 SQL

### Scenario: TaskPlanner 对超出数据范围拒答

给定一个用户查询问及当前数据库完全不包含的业务域
当 `TaskPlanner.plan()` 被调用时
则应返回 `PlanResult(verdict="reject")`
并且 `reject_reason` 应说明查询内容超出当前可访问数据范围
并且主流程应直接 END

### Scenario: TaskPlanner 三选一裁决由 LLM 完成

给定任意用户查询
当 `TaskPlanner.plan()` 被调用时
则应通过 LLM 以强制 JSON 输出完成裁决
并且输出应包含 verdict / intent_type / subqueries / ambiguities / clarify_question / reject_reason 字段
并且解析失败时应降级为 `verdict="execute"` 单意图执行（不阻塞主流程）

### Scenario: 反问通过 LangGraph interrupt 暂停等待用户回答

给定 `TaskPlanner` 返回 `verdict="clarify"` 且未达反问上限
当主图执行到 task_planner 节点时
则应通过 `langgraph.types.interrupt` 暂停图执行
并且 interrupt 的 value 应为结构化的反问上下文（含 clarify_question / ambiguities / clarify_round）
并且图应挂起在 `InMemorySaver` checkpointer 中
并且当前 SSE 流应以 `clarification` 事件推送反问问题后正常结束（status=200）

### Scenario: 用户回答后从中断点恢复执行

给定主图因反问挂起在 checkpointer 中（thread_id=会话ID）
当用户通过 clarify resume 请求提交回答时
则应调用 `graph.stream(Command(resume=回答), config)`
并且 graph 应从中断点恢复，将回答注入 TaskPlanner 重新规划
并且重新规划时应携带用户回答作为上下文
并且若回答消除了歧义则进入 EXECUTE 路径，否则再次 CLARIFY（受上限约束）

### Scenario: 同一会话多轮反问通过 thread_id 共享状态

给定同一会话（session_id）已发生一轮反问且挂起
当用户提交该轮回答时
则应使用与首次执行相同的 `thread_id`（即 session_id）恢复
并且 checkpointer 应还原上一轮的全部状态（含 clarify_round 计数）
并且 clarify_round 应在每次反问后递增

### Scenario: 最多 5 轮反问后强制退出

给定单次用户查询的反问轮次 `clarify_round` 已达到 5
当 `TaskPlanner.plan()` 第 6 次被调用时
则不应再触发 interrupt
并且应降级为 `verdict="execute"` 用最佳猜测执行（或 verdict="reject" 若完全无法猜测）
并且 `clarify_history` 应记录"达到反问上限"事件
并且应通过 MemoryUpdater 写入 UserMemory

### Scenario: 用户拒答时立即放行

给定用户在反问中回答 "不知道" / "跳过" / "算了" / "skip" / "不清楚" / "随便" 之一
当 `DialogManager` 识别为拒答关键词时
则应立即退出反问循环
并且应基于原始查询用最佳猜测继续执行
并且应记录拒答事件到 UserMemory

### Scenario: 拒答关键词可配置

给定 `config/clarification.yaml` 中 `decline_keywords` 列表
当 `DialogManager` 初始化时
则应加载配置中的拒答关键词列表
并且默认列表为 ["不知道","跳过","算了","skip","不清楚","随便"]
并且配置可覆盖默认列表

### Scenario: 多子查询串行执行并隔离失败

给定 TaskPlanner 分解出 N（N>1）个子查询
当 `SubqueryOrchestrator.run()` 被调用时
则应逐个把子查询喂给单查询子图（ir → ss → answerability_check → cg → execution → decision）
并且每个子查询的 `final_decision` 应收集进 `subquery_results`
并且某个子查询全失败不应中断其他子查询执行
并且每个子查询应携带各自的 decision_path 与失败原因

### Scenario: 单查询子图与主图复用同一套节点工厂

给定需要支持单意图和多意图两种执行路径
当构建执行链路时
则应抽取出 `build_single_query_graph()` 工厂（ir → ss → answerability_check → cg → execution → decision）
并且主图单意图路径和多意图 orchestrator 均应复用该工厂
并且不应出现两套重复的节点实现

### Scenario: 总结模块对多结果生成汇总回答

给定多个子查询均产生结果
当 `aggregate_results` 节点执行时
则应调用 LLM 将多个子结果汇总为一段连贯的自然语言回答
并且汇总回答应按子查询顺序组织
并且每个子结果应标注其来源子查询

### Scenario: 总结模块按需调用 LLM 节约 token

给定执行结果为单子查询且无数据表
当 `aggregate_results` 节点执行时
则应直接透传单结果，不调用 LLM 总结
并且只有多子查询或有数据表时才调用 LLM 汇总

### Scenario: 数据表结果以结构摘要喂给总结器而非整表

给定某子查询结果为数据表（多行多列）
当 `aggregate_results` 准备总结输入时
则应仅提取「列名 + 行数 + 头部样本（前 5 行）」作为结构摘要喂给总结 LLM
并且不应把原始结果集整表喂给总结 LLM
并且原始完整结果应通过 state 透传给前端渲染
并且该摘要策略与现有 ResultVerifier（列名+前5行）思路一致

### Scenario: 拒答路径返回结构化拒答原因

给定 TaskPlanner 返回 `verdict="reject"` 或反问达上限
当主图走拒答路径时
则应设置 `rejection_reason` 字段
并且应通过 SSE `result` 事件返回拒答原因（而非数据）
并且不应执行任何 SQL 或返回数据表

### Scenario: 反问节点前置到 IR 之前

给定新主图拓扑
当本变更集成后
则 `task_planner` 节点应位于 `history_cache` 之后、`ir` 之前
并且 IR 后不再有 clarification 节点（原占位节点移除）
并且流程为 `START → history_cache → task_planner → (EXECUTE/CLARIFY/REJECT 分支)`

### Scenario: interrupt 必须配 checkpointer

给定主图使用了 `interrupt()` 机制
当编译主图时
则应通过 `graph.compile(checkpointer=InMemorySaver())` 启用 checkpointer
并且 config 中应设置 `configurable.thread_id`（取值为 session_id）
否则 interrupt 将无法恢复图状态

### Scenario: 反问轮次与历史记录持久化

给定一次反问完成（用户给出有效回答或拒答或达上限）
当 MemoryUpdater 执行时
则应将本次反问历史（问题、用户回答、是否澄清成功）写入 UserMemory
并且应调用 `UserMemory.append_clarification()` 记录
并且 `clarified_keywords` 应被注入 state 供后续 SS/CG 使用

### Scenario: TaskPlanner 可通过配置整体关闭

给定 `config/clarification.yaml` 中 `enabled: false`
当主图构建时
则 `task_planner` 节点应退化为直接 EXECUTE 单意图（跳过 LLM 裁决）
并且不触发任何反问
并且不影响其他节点

### Scenario: resume 请求与首次请求复用查询入口

给定用户需要回答反问
当客户端发起 resume 请求时
则应通过 `/api/v1/query` 接口（新增 `resume` 字段）或独立 `/api/v1/query/clarify` 接口提交
并且请求应携带 session_id 与 answer
并且后端应据此构造 `Command(resume=answer)` 并用同一 thread_id 恢复
并且恢复结果应继续以 SSE 流式输出

### Scenario: 拒答/不可回答分层不互相替代

给定系统中同时存在 task_planner（IR 前）与 answerability_check（SS 后）
当二者都启用时
则 task_planner 负责拦截"问得不清楚"（意图层）
并且 answerability_check 负责拦截"答得不对题"（数据维度层）
并且二者并存分层，不互相替代

## ADDED Requirements

#### Scenario: 召回为空时触发反问（条件 A）
给定一个用户查询经过 IR 模块后所有候选数为 0
当 `TriggerDetector.detect()` 被调用时
则应返回 `TriggerResult(triggered=True, type="A", reason="召回为空")`
并且后续 `QuestionGenerator` 应生成「无法定位字段，请补充信息」类反问
并且不应调用 Tavily 联网搜索

#### Scenario: 语义不匹配时触发反问并联网补充领域知识（条件 B）
给定一个查询关键词 "苹果" 召回到 Top-1 值 "Apple Inc." 但上下文为食品类目
当 `TriggerDetector` 通过 LLM 判定语义不一致时
则应返回 `TriggerResult(triggered=True, type="B", reason="语义不匹配")`
并且 `WebSearchEnricher` 应被调用以 Tavily 搜索 "苹果" 的领域定义
并且搜索结果应作为上下文喂给 `QuestionGenerator`
并且搜索结果**不得**直接展示给用户

#### Scenario: 相似度过低时触发反问（条件 C）
给定一个查询关键词的 Top-1 向量相似度 < 0.4 或 LSH Jaccard < 0.3
当 `TriggerDetector.detect()` 被调用时
则应返回 `TriggerResult(triggered=True, type="C", reason="相似度过低")`
并且不调用 Tavily 搜索

#### Scenario: 与用户历史偏好冲突时触发反问（条件 D）
给定用户历史 `term_preferences` 中 "销售额" 映射到 "gmv"，而本次 IR 返回 "net_revenue"
当 `TriggerDetector.detect()` 被调用时
则应返回 `TriggerResult(triggered=True, type="D", reason="与历史偏好冲突")`
并且 `QuestionGenerator` 应在反问中提示用户其历史习惯

#### Scenario: 联网搜索结果会话内缓存复用
给定单次 LangGraph 运行内同一关键词触发了两次 B 类反问
当 `WebSearchEnricher.search("苹果")` 被第二次调用时
则应直接返回会话内缓存结果而不发起 Tavily 调用
并且日志中应记录「缓存命中」事件

#### Scenario: Tavily 失败时降级
给定 Tavily API 调用超时或返回错误
当 `WebSearchEnricher.search()` 异常时
则应捕获异常并返回空列表
并且 `QuestionGenerator` 应仅基于现有上下文生成反问，不阻塞主流程

#### Scenario: 反问粒度由 LLM 自适应选择
给定一个查询缺失关键维度（如"卖了多少"未指明商品类别）
当 `QuestionGenerator.generate()` 被调用时
则应生成粗粒度反问（如"您想查询哪类商品的销量？"）
反之，若仅是某个具体值映射不确定
则应生成细粒度反问（如"您说的'苹果'是 product_name='Apple' 还是 brand='Apple Inc.'？"）

#### Scenario: 反问通过 LangGraph interrupt 暂停等待
给定 `UserDialog.ask(question)` 被调用
当 LangGraph 执行到该节点时
则应通过 `langgraph.types.interrupt` 暂停图执行
并且通过 MCP 协议将问题暴露给前端
并且用户回答后图应从中断点恢复执行

#### Scenario: 最多 5 轮反问后强制退出
给定单次用户查询已触发 5 次反问
当 `TriggerDetector.detect()` 第 6 次被调用时
则应返回 `TriggerResult(triggered=False, reason="达到反问上限")`
并且 `MemoryWriter` 应记录"未澄清成功"事件到 UserMemory
并且主流程应继续走原始 IR 结果生成 SQL

#### Scenario: 用户拒答时立即放行
给定用户在反问中回答 "不知道" / "跳过" / "算了" / "skip" / "不清楚" / "随便" 之一
当 `DialogManager` 识别为拒答关键词时
则应立即退出反问循环
并且 `MemoryWriter` 应记录拒答事件
并且主流程应基于原始 IR 结果继续

#### Scenario: 澄清结果回写用户记忆
给定一轮反问成功完成（用户给出有效回答）
当 `MemoryWriter` 在子图末尾被执行时
则应调用 `UserMemory.record_term_preference()` 更新术语偏好
并且应调用 `UserMemory.append_clarification()` 记录本次对话
并且 `clarified_keywords` 应被注入 `NL2SQLState` 供 SS 使用

#### Scenario: 触发器可通过配置逐项关闭
给定 `config/clarification.yaml` 中 `triggers.D: false`
当出现与用户历史偏好冲突的场景时
则 `TriggerDetector` 应跳过 D 类判断
并且不触发反问

#### Scenario: 反问 Agent 插入到 IR 与 SS 之间
给定 LangGraph 主图编排了 IR → SS → CG 流程
当本变更被集成后
则 `clarification` 节点应插入在 IR 之后、SS 之前
并且 SS 应能读取 `state["clarified_keywords"]` 覆盖原始查询关键词

## Why

当前项目需要实现一个智能问数Agent系统，能够将自然语言查询转换为准确的SQL语句。现有的NL2SQL解决方案往往缺乏以下关键能力：
- 多阶段信息检索和schema选择
- 安全的SQL生成和执行
- 基于self-consistency的多候选验证机制
- 端到端的监控和调试能力

参考CHESS项目的优秀实践，结合BIRD-SQL数据集和Qwen大模型的能力，我们需要构建一个完整的多Agent NL2SQL系统，以满足生产级别的准确性和安全性要求。

此外，NL2SQL Pipeline 在 IR（信息检索）阶段存在两类「无声失败」：

1. **召回为空 / 召回过弱**：用户提到的关键词在 LSH/向量检索中找不到匹配，或最高相似度低于阈值。系统继续向下走会导致 SQL 生成偏离用户意图。
2. **语义不匹配**：召回到了值，但召回的值与用户的常用语境或当前上下文明显不符（例如用户说"苹果"，期望水果，但系统召回了"苹果公司"）。

这些情况下，与其让 Agent 盲目猜测继续生成错误 SQL，不如**主动反问用户**澄清意图。同时：
- 反问需要**领域知识支撑**：当系统不理解某个领域术语时，应能通过联网搜索补充背景知识，再生成更精准的反问。
- 反问需要**用户画像支撑**：不同用户的用词习惯不同（"销售额"对 A 用户指 GMV，对 B 用户指净收入），需基于用户长期记忆判断是否触发反问，以及如何反问。

## What Changes

本变更将引入一个完整的NL2SQL Agent系统，包含以下核心组件：

1. **预处理模块**：为BIRD-SQL数据集中的所有数据库字段创建LSH索引和BGE-M3向量嵌入
2. **信息检索(IR)模块**：实现关键词提取、LSH值检索和语义schema检索的两阶段策略
3. **表关联图(Schema Relationship Graph)**：预处理阶段构建表间JOIN关系图，运行时注入JOIN路径到Prompt
4. **Schema选择器(SS)模块**：基于M-schema格式进行列相关性过滤
5. **候选SQL生成器(CG)模块**：支持实体掩码、few-shot选择、多SQL生成和安全验证
6. **执行与决策模块**：实现安全的SQL执行、错误修正循环和self-consistency投票决策
7. **监控集成**：与LangSmith集成，提供全流程监控和流式输出
8. **反问 Agent (ClarificationAgent) 与用户记忆 (UserMemory)**：插入到 IR 与 SS 之间的 LangGraph 流程中，主动澄清歧义并积累用户长期偏好
9. **可回答性检查 (Answerability Check)**：SS 之后、CG 之前插入的轻量判断节点，宽松原则——只有明确无法回答时才拦截，避免浪费后续 LLM 调用
10. **结果可信度验证 (Result Verification)**：增强 Decision 节点，严格验证最终 SQL 语义是否与用户问题对齐，防止"答非所问"的硬凑输出

```
用户输入
   │
   ▼
[IR 信息检索]   ─→ 召回结果 + 检索元数据（相似度分数、命中数等）
   │
   ▼
[ClarificationAgent]
   ├─ ① TriggerDetector：判断是否需要反问（4 类触发条件）
   ├─ ② WebSearchEnricher（条件 B 时启用）：通过 Tavily MCP 补充领域知识
   ├─ ③ QuestionGenerator：基于上下文 + 用户记忆 + 搜索结果生成反问
   ├─ ④ UserDialog：暂停流程，等待用户回答（最多 5 轮，拒答则放行）
   └─ ⑤ MemoryWriter：将本轮澄清结果写回 UserMemory
   │
   ▼
[SS Schema 选择]  ─→ 携带澄清后的关键词与上下文继续后续流程
   │
   ▼
[可回答性检查]   ─→ 宽松判断：有明显缺失/粒度不匹配 → 拒答(含原因) → END
   │                      否则/不确定 → 继续
   ▼
[CG SQL 生成] → [Execution] → [Decision + 结果验证]
                                      │
                                      ├─ 可信 → 返回结果
                                      └─ 不可信 → 拒答(含原因) → END
```

系统将在conda虚拟环境NL2SQL中运行，通过Terminal提供交互式对话界面。

## Capabilities

### New Capabilities
- `nl2sql-preprocessing`: 为数据库schema和字段值创建LSH索引和向量嵌入
- `information-retrieval`: 实现两阶段信息检索策略（LSH + 语义相似性），含四向同义词扩写、向量粗召回(top_k=50) + N-gram 投票精排
- `schema-relationship-graph`: 预处理阶段构建表间关联图（显式FK + 向量匹配 + 命中率验证 + LLM辅助），运行时BFS提取JOIN路径注入Prompt
- `schema-selection`: 基于M-schema格式的智能列选择
- `sql-generation`: 安全的多候选SQL生成，包含危险操作过滤和语法验证
- `execution-engine`: 安全的SQL执行引擎，支持SQLite和MySQL数据库连接
- `self-consistency`: 基于多数一致性和LLM最终决策的投票机制
- `monitoring-integration`: LangSmith全流程监控和Terminal流式输出
- `user-memory`: 基于 JSON 文件的用户长期记忆管理，支持按 `user_id` 维度记录用户的术语偏好、领域上下文、历史澄清结果
- `clarification`: 多步反问 Agent，含触发检测、联网搜索补充、反问生成、用户对话循环、记忆回写五个子流程
- `answerability-verification`: 两阶段拒答机制——SS 后的可回答性检查（宽松）+ Decision 后的结果可信度验证（严格），防止 LLM 在信息不足时硬凑答非所问的 SQL

## Impact

**受影响的代码和系统**：
- 新增核心模块目录：`src/preprocessing/`, `src/retrieval/`, `src/schema_selection/`, `src/sql_generation/`, `src/execution/`, `src/decision/`
- 新增预处理产物：`data/preprocessed/schema_graphs/{db_id}.json`（表关联图JSON邻接表）
- 新增模块：`src/clarification/`（含 `agent.py`、`trigger.py`、`web_search.py`、`question_generator.py`、`dialog.py`）
- 新增模块：`src/memory/`（含 `user_memory.py`、`storage.py`）
- 数据目录：新增 `data/user_memory/{user_id}.json` 存储用户长期记忆
- LangGraph 工作流：新增 `clarification` 节点，位置在 IR 之后、SS 之前；新增 `answerability_check` 节点，位置在 SS 之后、CG 之前；增强 `decision` 节点，内含结果可信度验证
- 依赖新增：BGE-M3 embedding模型、ChromaDB向量数据库、sqlglot SQL验证库、nltk实体识别、`tavily-python`（MCP 协议接入）
- 数据库连接：支持SQLite（优先）和MySQL数据库格式
- 环境配置：conda虚拟环境NL2SQL，已配置Qwen API和LangSmith密钥；新增 Tavily API Key 环境变量（`TAVILY_API_KEY`）
- 用户界面：Terminal交互式对话，流式输出执行过程和思考过程
- 检索接口微调：`InformationRetriever.retrieve()` 返回值需包含 `scores` 与 `metadata`

**非功能影响**：
- **延迟**：触发反问时单次澄清增加 ~3-5s（含 1 次 LLM + 可选 1 次 Tavily 调用）；未触发时无影响。可回答性检查增加 ~2-3s（1 次 LLM 调用），但拦截时可省去后续 CG + Exec 的 ~30-120s 开销。
- **成本**：Tavily 免费层 1000 次/月，搜索结果会话内缓存，避免重复调用。
- **用户体验**：交互模式从「一问一 SQL」变为「可能多轮澄清」，需在 Terminal UI 明确告知用户。

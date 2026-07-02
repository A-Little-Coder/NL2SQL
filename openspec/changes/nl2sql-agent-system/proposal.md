## Why

当前项目需要实现一个智能问数Agent系统，能够将自然语言查询转换为准确的SQL语句。现有的NL2SQL解决方案往往缺乏以下关键能力：
- 多阶段信息检索和schema选择
- 安全的SQL生成和执行
- 基于self-consistency的多候选验证机制
- 端到端的监控和调试能力

参考CHESS项目的优秀实践，结合BIRD-SQL数据集和Qwen大模型的能力，我们需要构建一个完整的多Agent NL2SQL系统，以满足生产级别的准确性和安全性要求。

此外，NL2SQL Pipeline 在用户意图层面存在三类需要前置处理的场景：

1. **表述歧义**：用户提到的实体多义（"苹果"指公司或水果）、粒度不明、缺失关键限定（时间/维度）。盲目进入 IR 召回会基于错误假设生成偏离意图的 SQL。
2. **多意图复合查询**：用户一句话包含多个独立问数意图（"查苹果的销售额和利润，再对比去年"），单次 SQL 无法覆盖，需分解为多个子查询逐个执行。
3. **越权 / 超范围**：用户查询包含写操作意图（删除/更新/插入）或问及当前数据库完全不包含的业务域，应直接拒答而非尝试生成。

这些情况下，与其让 Agent 盲目进入 IR 召回并生成错误 SQL，不如在 IR 之前新增**意图理解层（TaskPlanner）**做三选一裁决：清晰→分解执行、歧义→反问澄清、不可答→拒答。反问采用 LangGraph 1.x 的 `interrupt()` 暂停等用户回答后恢复。同时：
- 反问需要**用户画像支撑**：不同用户的用词习惯不同（"销售额"对 A 用户指 GMV，对 B 用户指净收入），需基于用户长期记忆判断歧义与反问方式。
- 多次查询结果需**总结汇总**：多子查询的结果要汇总成连贯回答，且数据表结果要避免整表喂给 LLM 浪费 token。

> 2026-06-29 重大重新定义：原方案（IR 之后基于召回结果触发四类反问 + Tavily 联网）已废弃，改为 IR 之前的前置 TaskPlanner + interrupt + 多意图分解 + 结果总结。WebSearch/Tavily 与 IR 后 TriggerDetector 本期跳过。

## What Changes

本变更将引入一个完整的NL2SQL Agent系统，包含以下核心组件：

1. **预处理模块**：为BIRD-SQL数据集中的所有数据库字段创建LSH索引和BGE-M3向量嵌入
2. **信息检索(IR)模块**：实现关键词提取、LSH值检索和语义schema检索的两阶段策略
3. **表关联图(Schema Relationship Graph)**：预处理阶段构建表间JOIN关系图，运行时注入JOIN路径到Prompt
4. **Schema选择器(SS)模块**：基于M-schema格式进行列相关性过滤
5. **候选SQL生成器(CG)模块**：支持实体掩码、few-shot选择、多SQL生成和安全验证
6. **执行与决策模块**：实现安全的SQL执行、错误修正循环和self-consistency投票决策
7. **监控集成**：与LangSmith集成，提供全流程监控和流式输出
8. **反问机制（TaskPlanner）与用户记忆（UserMemory）**：在 IR 之前插入意图理解层，三选一裁决（执行/反问/拒答），支持多意图分解、interrupt 暂停恢复与结果总结，并积累用户长期偏好
9. **可回答性检查 (Answerability Check)**：SS 之后、CG 之前插入的轻量判断节点，宽松原则——只有明确无法回答时才拦截，避免浪费后续 LLM 调用
10. **结果可信度验证 (Result Verification)**：增强 Decision 节点，严格验证最终 SQL 语义是否与用户问题对齐，防止"答非所问"的硬凑输出

```
用户输入
   │
   ▼
[history_cache 历史命中]  ─→ 命中 → 直接执行缓存 SQL
   │ 未命中
   ▼
[task_planner 意图理解]  ─→ 三选一裁决（LLM 强制 JSON）
   ├─ EXECUTE (single/multi) → 分解子查询
   ├─ CLARIFY → interrupt 暂停 → 等用户回答 → resume 重新规划（最多 5 轮，拒答放行）
   └─ REJECT → 拒答(含原因) → END
   │ EXECUTE
   ▼
[run_subqueries]  ─→ 单意图直接执行 / 多意图 orchestrator 串行
   │                  复用 build_single_query_graph(): ir→ss→answerability→cg→execution→decision
   ▼
[aggregate_results 总结]  ─→ 按需 LLM 汇总；数据表用结构摘要（列名+行数+前5行）降 token
   │
   ▼
[memory_update 记忆学习]  ─→ 反问历史回写 UserMemory + 自动学习
   │
   ▼
[可回答性检查]（单查询子图内，SS 之后）→ 宽松判断：明显缺失/粒度不匹配 → 拒答 → END
   │
[CG] → [Execution] → [Decision + 结果验证] → 可信返回结果 / 不可信拒答
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
- `clarification`: IR 之前的意图理解层（TaskPlanner），三选一裁决（执行/反问/拒答）+ 多意图分解 + interrupt 暂停恢复 + 结果总结，含反问对话循环与记忆回写
- `answerability-verification`: 两阶段拒答机制——SS 后的可回答性检查（宽松）+ Decision 后的结果可信度验证（严格），防止 LLM 在信息不足时硬凑答非所问的 SQL

## Impact

**受影响的代码和系统**：
- 新增核心模块目录：`src/preprocessing/`, `src/retrieval/`, `src/schema_selection/`, `src/sql_generation/`, `src/execution/`, `src/decision/`
- 新增预处理产物：`data/preprocessed/schema_graphs/{db_id}.json`（表关联图JSON邻接表）
- 新增模块：`src/clarification/`（含 `task_planner.py`、`dialog.py`、`subquery_orchestrator.py`、`result_summarizer.py`、`agent.py`）
- 新增模块：`src/graph/single_query_graph.py`（单查询子图工厂）
- 新增模块：`src/memory/`（含 `user_memory.py`、`storage.py`）
- 数据目录：新增 `data/user_memory/{user_id}.json` 存储用户长期记忆
- LangGraph 工作流：新增 `task_planner` 节点（IR 之前）、`run_subqueries`、`aggregate_results` 节点；主图编译启用 `InMemorySaver` checkpointer（interrupt 必需）；移除原 IR 后 `clarification` 占位节点；保留 `answerability_check`（SS 之后）
- 依赖新增：BGE-M3 embedding模型、ChromaDB向量数据库、sqlglot SQL验证库、nltk实体识别（原 `tavily-python` 本期跳过）
- 数据库连接：支持SQLite（优先）和MySQL数据库格式
- 环境配置：conda虚拟环境NL2SQL，已配置Qwen API和LangSmith密钥；新增 `config/clarification.yaml`（反问开关/上限/拒答关键词）
- 用户界面：Terminal交互式对话，流式输出执行过程和思考过程；支持反问 interrupt/resume
- 检索接口微调：`InformationRetriever.retrieve()` 返回值需包含 `scores` 与 `metadata`

**非功能影响**：
- **延迟**：触发反问时单次澄清增加 ~2-3s（1 次 LLM 裁决 + interrupt 暂停等待）；未触发时仅增加 1 次 TaskPlanner LLM 调用 ~2s。可回答性检查增加 ~2-3s，但拦截时可省去后续 CG + Exec 的 ~30-120s 开销。
- **成本**：TaskPlanner 每次查询 1 次 LLM（可配置关闭）；总结模块按需调用（单结果无表不调 LLM）；数据表结构摘要避免大表 token 爆炸。
- **用户体验**：交互模式从「一问一 SQL」变为「可能多轮澄清」，需在 Terminal UI 明确告知用户；多意图查询会得到汇总后的连贯回答。

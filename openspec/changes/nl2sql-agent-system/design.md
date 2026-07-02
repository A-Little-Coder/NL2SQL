## Context

当前项目处于初始阶段，仅有基础的数据下载脚本（`download_data.py`）和OpenSpec配置。BIRD-SQL数据集包含多个数据库实例，每个实例都有对应的schema、表结构和样本数据。我们需要构建一个完整的NL2SQL系统，能够处理自然语言查询并生成准确、安全的SQL语句。

此外，本设计文档还涵盖「反问 Agent + 用户记忆」两大能力的实现细节，包括触发条件、外部集成、对话循环、存储格式与决策依据。

## Goals / Non-Goals

**Goals:**
- 实现端到端的NL2SQL Agent系统，支持自然语言到SQL的转换
- 集成多阶段信息检索（关键词提取 + LSH值检索 + 语义schema检索）
- 实现M-schema格式的智能列选择机制
- 支持多候选SQL生成（最多5个）和安全验证
- 实现self-consistency投票决策：多数一致时选择最快SQL，全不同时由LLM最终决策
- 提供Terminal交互式界面和LangSmith全流程监控
- 支持SQLite和MySQL数据库连接
- 在conda虚拟环境NL2SQL中运行
- 提供反问机制（TaskPlanner）在 IR 之前做意图理解与三选一裁决（执行/反问/拒答），支持多意图分解、interrupt 暂停恢复与结果总结，并通过用户长期记忆持续优化

**Non-Goals:**
- 前端Web界面开发（仅Terminal交互）
- 支持除SQLite/MySQL外的其他数据库类型
- 实时协作功能
- 用户认证和权限管理
- 大规模分布式部署

## Decisions

**1. 数据库连接策略**
- **决策**: 优先使用SQLite连接，因为BIRD-SQL mini版本主要以SQLite格式提供
- **理由**: Python内置sqlite3模块，无需额外依赖，简化开发和部署

**2. 向量数据库选择**
- **决策**: 使用ChromaDB作为向量存储
- **理由**: 轻量级、易于集成、支持持久化，适合开发和小规模部署

**3. Embedding模型**
- **决策**: 使用BGE-M3模型
- **理由**: 中文场景表现优秀，支持dense/sparse/colbert多功能统一embedding

**4. SQL安全验证**
- **决策**: 结合sqlglot静态验证 + 数据库EXPLAIN动态验证
- **理由**: sqlglot支持多SQL方言且轻量，EXPLAIN提供最准确的语法验证

**5. Self-consistency决策逻辑**
- **决策**: 
  - 多数结果一致 → 选择执行时间最短的SQL
  - 所有结果不同 → 调用LLM进行最终决策
  - 全部失败 → 返回错误
- **理由**: 结合统计一致性和智能判断的优势，提高准确率

**6. 错误修正机制**
- **决策**: 最多2次错误修正循环，提供详细错误信息给LLM
- **理由**: 平衡修正效果和计算成本，避免无限循环

**7. 监控集成**
- **决策**：通过环境变量驱动 LangSmith 自动接入（路径 A），不实现自定义 `LangSmithMonitor` 包装类。
- **背景**：LangChain 1.x + LangGraph 原生支持 LangSmith：当 `LANGCHAIN_TRACING_V2=true` 且 `LANGCHAIN_API_KEY` 非空时，所有 `ChatOpenAI` 调用与 `StateGraph` 节点会自动上报 trace，业务代码无需任何改动。
- **理由**：
  1. **零侵入**：`utils/llm_client.py`、`main_graph._wrap_node`、各 Agent 子图都不需要插入 monitor 调用，避免与 LangGraph 自动 span 双重嵌套。
  2. **嵌套自动**：LangGraph 主图 → 子图 → LLM 调用形成天然 span 树，trace 拓扑与代码拓扑一致。
  3. **维护成本低**：升级 LangChain/LangGraph 时无需同步维护 Monitor 适配代码。
- **被否决方案**：手写 `LangSmithMonitor.trace(...)` 上下文管理器
  - 与 LangGraph 自动 span 形成双重嵌套，trace 树混乱
  - 每个节点都要包一层 `with monitor.trace(...)`，样板代码多
  - 现有 `src/monitor/langsmith_monitor.py` stub 即此路线遗留，需在落地任务中清理

**7a. 命名层（路径 A 之上的可读性增强）**
- **决策**：在路径 A 自动接入之上，显式给图 / LLM 调用起 `run_name`，让 LangSmith UI 可读。
- **三个层次**：
  1. **项目级**：`LANGCHAIN_PROJECT=NL2SQL`（全大写），一个项目一个 LangSmith 面板。
  2. **图级 run_name**：主图 `nl2sql-pipeline`；子图 `ir-graph` / `ss-graph` / `cg-graph` / `execution-graph` / `decision-graph`。在 `build_*_graph()` 编译末尾用 `compile().with_config(run_name=...)` 钉死。
  3. **LLM 调用级 run_name**：`LLMClient.invoke / stream / ainvoke / astream` 4 个公开方法新增 `run_name: Optional[str] = None` 参数。内部走 `self._chat_model.with_config(run_name=...).bind(**kw)`（注意 `with_config` 与 `bind` 的语义差异：前者管 runtime 配置，后者管 model 参数）。业务侧 9+ 处调用点统一起名：`cache-check` / `ir-keywords` / `ir-synonyms` / `answer-check` / `ss-relevance` / `cg-generate` / `exec-smartfix` / `decision-r1` / `decision-r2` / `join-inference` / `clarify-question`。
- **不再是"零代码"**：约 15 处增量改动，每处 1-3 行。代价换 LangSmith dashboard 可读。

**7b. 请求级追踪（query_id 与 LangSmith metadata）**
- **决策**：每次 HTTP 请求生成 `query_id = uuid4().hex[:12]`，作为该请求在日志、SSE、LangSmith 三处的统一关联 ID。
- **生成位置**：`src/api/routes/query.py` 的 `query_endpoint` 入口第一行。
- **三处使用**：
  1. **日志**：`query_endpoint` 入口/出口/异常 `logger.info(f"[query_id={query_id}] ...")`；`main_graph._wrap_node` 装饰器在每个节点 enter/exit 自动追加 `[qid={state['query_id']}]`，使 13 个节点的日志全部可关联。
  2. **SSE**：所有 SSE 事件 payload 都带 `query_id` 字段（不仅 `done`）。前端可按 `query_id` 分组渲染、定位上下文。
  3. **LangSmith**：在 `graph.stream(state, config=...)` 处一次性注入 `configurable.thread_id=session_id` + `run_name=f"query-{query_id}"` + `tags=[db_id, "api", f"user:{user_id}"]` + `metadata={query_id, user_id, session_id, db_id, user_query[:200]}`。LangSmith UI 可按 `metadata.query_id` 精确定位单次请求；按 `thread_id` 聚合多轮会话。
- **state 字段**：`NL2SQLState` 新增 `query_id: str` 字段，节点内部如需打日志显式 `state.get("query_id", "")` 取出（与节点内业务日志的接入风格保持一致，不引入 ContextVar 魔法）。
- **独立性**：`query_id` 基础设施（生成 + state + 日志 + SSE）是独立子任务（§8.0），不依赖 LangSmith；LangSmith config 注入（§8.1.7）依赖 §8.0 完成。
- **理由**：
  1. **可定位**：用户报 bug 时直接给 `query_id`，后端日志、SSE 重放、LangSmith 三方 1 秒定位。
  2. **零额外成本**：`uuid4().hex[:12]` 生成几乎零开销，12 位短 hex 在日志里可读。
  3. **解耦**：日志可观测性（§8.0）与 LangSmith 接入（§8.1）两件事独立，便于阶段性交付。

---

## 关键设计决策（反问 Agent + 用户记忆）

### 决策 8：用户概念双轨制（系统用户 + 业务用户）

**决策**：本期同时支持两种 `user_id` 来源：
- **系统用户**：登录账号或会话 ID（如 `session_xxx`）。
- **业务用户**：用户在 SQL 上下文中的语义身份（如"销售部小王"）。

**理由**：用户明确表示"两个都做"。系统用户保证记忆隔离的硬约束，业务用户允许同一系统账号下根据角色切换记忆视图。

**实现**：`UserMemory` 类构造时接收 `user_id`（必填）+ `role_tag`（可选）。文件名采用 `{user_id}__{role_tag}.json`，未设 `role_tag` 时退化为 `{user_id}.json`。

---

> ⚠️ **2026-06-29 重大重新定义**：反问机制从「IR 之后基于召回结果触发四类反问 + Tavily 联网」改为「IR 之前的前置意图理解（TaskPlanner）+ interrupt 暂停恢复 + 多意图分解 + 结果总结」。下方决策 9–15 已按新方案重写。原 WebSearch/Tavily（决策 9/14/15）与 IR 后 TriggerDetector 本期跳过，不实现。新方案三选一裁决：EXECUTE / CLARIFY / REJECT。决策 10/11/12/13 保留并按新方案调整语义。

### 决策 9（重写）：反问定位前移到 IR 之前，作为意图理解层

**决策**：反问机制不再放在 IR 之后基于召回结果触发，而是在 `history_cache` 之后、`ir` 之前新增 `task_planner` 节点，作为「意图理解 + 任务规划」层。TaskPlanner 对用户输入做三选一裁决：

- **EXECUTE（执行）**：意图清晰。单意图直接执行；多意图分解为 N 个子查询逐个执行。
- **CLARIFY（反问）**：表述有歧义（实体多义、粒度不明、缺失关键限定）→ interrupt 暂停等用户澄清。
- **REJECT（拒答）**：越权写操作 / 超出数据范围 / 无法理解 → 直接 END 带拒答原因。

**理由**：用户明确"下一步需要开发反问这个机制，主要作用是消歧义，可以在一开始设计一个任务规划的节点，这个节点负责把多个查数的任务分解成一个个的问数工具可执行的 query。同时根据情况进行拒答。另外还需要对存在表述不清晰的问题进行反问"。前置到 IR 之前能在召回前就消除歧义，避免对错误召回结果做无谓的 SQL 生成；同时天然承担多意图分解职责。

**与 answerability_check 的关系**：`task_planner`（IR 前）拦截"问得不清楚"（意图层）；`answerability_check`（SS 后，决策 23）拦截"答得不对题"（数据维度层）。二者并存分层，不互相替代。

---

### 决策 10（重写）：反问粒度由 TaskPlanner 内联 LLM 自适应生成

**决策**：不再单独建 `QuestionGenerator` 类。反问问题由 `TaskPlanner` 在裁决为 CLARIFY 时直接生成 `clarify_question`，根据歧义类型自适应粒度：
- **粗粒度**：缺失关键维度时 → "您想查询哪类商品的销量？"
- **细粒度**：具体值映射不确定时 → "您说的'苹果'是指 product_name='Apple' 还是 brand='Apple Inc.'？"

**理由**：用户明确"两种反问的粒度都可能"。TaskPlanner 已在做意图分析，内联生成问题避免额外一次 LLM 调用与类拆分开销。

---

### 决策 12（重写）：反问采用 LangGraph 1.x `interrupt()` + `Command(resume=...)` + `InMemorySaver`

**决策**：反问暂停/恢复采用 LangGraph 1.x 函数式动态中断（非编译期 `interrupt_before/after`）：

- 暂停：`task_planner` 节点内 `verdict=="clarify"` 时调用 `langgraph.types.interrupt(clarify_context)`，抛 `GraphInterrupt`，图挂起。
- 恢复：用户回答后调用 `graph.stream(Command(resume=answer), config)`，graph 从中断点恢复，`interrupt()` 返回用户回答，TaskPlanner 重新规划。
- 持久化：`graph.compile(checkpointer=InMemorySaver())`，`config["configurable"]["thread_id"] = session_id`。interrupt 必须配 checkpointer，否则无法恢复状态。
- 检测暂停：流式时检查 `"__interrupt__" in chunk`；流结束后用 `graph.get_state(config).next` 二次确认。

**理由**：用户确认 MemorySaver。`interrupt()` 函数式动态中断只有真正需要消歧时才暂停，避免无谓中断；thread_id 映射到 session_id 保证同一会话多轮反问共享状态。经实测 langgraph 1.2.4 API 准确。

**API 准确性**（1.2.4 实测）：
- `from langgraph.types import interrupt, Command, Interrupt`
- `from langgraph.checkpoint.memory import InMemorySaver`（`MemorySaver` 为别名，推荐用 `InMemorySaver`）
- 首次执行：传 `initial_state` + `config`；恢复执行：传 `Command(resume=...)` + 同一 `config`（不再传 initial_state）
- resume 时节点从头重跑，`interrupt()` 不再抛异常而是返回 resume 值

---

### 决策 13（重写）：最多 5 轮反问，拒答则放行，计数器存 state

**决策**：
- 反问上限 5 次，计数器 `clarify_round` 存于 state（checkpoint 持久化），每次反问后递增。
- 达到上限时不再 interrupt，降级为 EXECUTE 用最佳猜测执行（或 REJECT 若完全无法猜测）。
- 用户输入「不知道 / 跳过 / 算了 / skip / 不清楚 / 随便」识别为拒答，立即退出反问循环，基于原始查询用最佳猜测继续。
- 拒答关键词列表可配置（`config/clarification.yaml` 的 `decline_keywords`）。

**实现模式**：硬上限检查 `if clarify_round >= 5` 放在 `interrupt()` 之前；计数器必须存 state（resume 时节点从头重跑，局部变量会丢失）。拒答识别后直接置 EXECUTE 路径。

**理由**：用户明确"连问 5 次。用户拒答就继续按原来的流程走就行"。避免对话陷入无限循环。计数器存 state 是 LangGraph interrupt 恢复语义的硬约束。

---

### 决策 14（重写）：多意图分解为子查询，单查询子图工厂复用

**决策**：TaskPlanner 裁决 EXECUTE 且 `intent_type=="multi"` 时，把用户查询分解为 N 个独立子查询。抽取出 `build_single_query_graph()` 工厂（封装 `ir → ss → answerability_check → cg → execution → decision` 整段），主图单意图路径和 `SubqueryOrchestrator` 多意图路径均复用该工厂，避免两套重复节点实现。

- `SubqueryOrchestrator.run(subqueries, shared_state)` 逐个把子查询喂给单查询子图，收集每个的 `final_decision` 进 `subquery_results`。
- **失败隔离**：某子查询全失败不中断其他子查询，各自带 decision_path 与失败原因。

**理由**：用户明确"全做"（含多意图分解）。复用单查询子图工厂避免逻辑重复，保证单/多意图行为一致。

---

### 决策 15（重写）：新增总结模块，按需调用 LLM，数据表以结构摘要降 token

**决策**：执行完成后新增 `aggregate_results` 节点做结果汇总：

- **按需触发**：单子查询且无数据表 → 直接透传，不调 LLM；多子查询或有数据表 → 调 LLM 汇总。节约 token。
- **多结果汇总**：多个子结果调 LLM 生成一段连贯自然语言回答，按子查询顺序组织，每个标注来源。
- **数据表降 token**：某子查询结果为数据表时，**仅提取「列名 + 行数 + 头部样本（前 5 行）」作为结构摘要喂给总结 LLM**，不把原始结果集整表喂入；原始完整结果通过 state 透传给前端渲染。该策略与现有 ResultVerifier（列名+前5行）思路一致。

**理由**：用户明确"最后应该有个总结模块，因为可能会执行多次查询。查询完的结果可能需要总结在一起输出。另外如果查数输出了数据表，这个看下怎么处理才能够节约总结模块的 token"。结构摘要降 token 是业界通用做法，避免大表撑爆 LLM 上下文。

---

### 决策 11：记忆存储采用 JSON 文件

**决策**：本期使用本地 JSON 文件，路径 `data/user_memory/{user_id}.json`（或带 role_tag）。结构如下：

```json
{
  "user_id": "session_abc123",
  "role_tag": null,
  "created_at": "2026-05-29T10:00:00",
  "updated_at": "2026-05-29T15:30:00",
  "term_preferences": {
    "销售额": {"resolved_to": "gmv", "confidence": 0.9, "last_used": "2026-05-29"},
    "苹果": {"resolved_to": "fruit_apple", "confidence": 0.8, "last_used": "2026-05-28"}
  },
  "domain_context": ["生鲜电商", "供应链"],
  "clarification_history": [
    {
      "timestamp": "2026-05-29T15:30:00",
      "original_query": "查一下苹果的销售额",
      "trigger_type": "B_semantic_mismatch",
      "question_asked": "您指的'苹果'是水果还是品牌？",
      "user_answer": "水果",
      "resolved_mapping": {"苹果": "fruit_apple"}
    }
  ]
}
```

**理由**：用户明确"先用 JSON 就行"。后续可平滑迁移到 SQLite/Redis。

**并发控制**：单用户串行写入，使用文件锁（`fcntl` on Unix / `msvcrt` on Windows）。

---

### 决策 16：隐私问题本期不处理

**决策**：用户记忆默认明文存储，不加密，不脱敏。

**理由**：用户明确"隐私问题先不考虑"。本期为原型，待生产化时再补隐私层。

---

## 关键设计决策（IR 检索策略升级）

### 决策 17：值检索采用「LSH 粗召回 + 语义向量精排」两阶段

**决策**：值检索（针对查询中的实体名称/字段值）保留 LSH 粗召回，但在其后追加一阶语义精排：

1. 对每个 keyword，先用 LSH（datasketch MinHash）召回 top N（默认 N=10）个相似值。
2. 对 (keyword, 候选值) 对用 BGE-M3 计算 embedding，取余弦相似度。
3. 过滤掉 embedding 相似度 < `value_semantic_threshold`（默认 0.6）的候选。
4. 返回精排后的值列表，附带 LSH Jaccard 分数与 embedding 分数两个维度。

**理由**：纯 LSH 召回是字符 n-gram 的 Jaccard，对"语义相近字面不同"的值（如 query 用"上海"，库里写"Shanghai"）束手无策。引入语义精排既保留 LSH 的快速字面召回能力，又借 embedding 兜底语义匹配。比 CHESS 省去了中间的"编辑距离"步骤（LSH 已覆盖字面）。

**实现要点**：
- 复用已为 schema 检索加载的 BGE-M3 模型，无额外资源开销。
- 单次 query 的精排开销：N（LSH top）× M（keyword 数）次 embedding 计算，CPU 实测可控制在 200ms 内。

---

### 决策 18：表/列 schema 检索采用「按关键词分组召回 + 组内 N-gram 投票精排」

**决策**：schema 检索按原生关键词分组独立召回，每个关键词返回自己的 top 5 列。

**流程**：

1. **关键词提取 + 同义词扩写**：LLM 提取若干原生关键词，每个关键词附带中英文同义词（详见决策 21）。关键词按原生 phrase 分组，每组包含 `[phrase, zh_synonyms..., en_synonyms...]`。
2. **按组独立向量粗召回**：每个关键词组内，对组内所有检索词分别做向量查询 top_k=50，组内取并集（去重）。
3. **组内 N-gram 投票精排**：在每组并集内，用该组自己的检索词列表做 3-gram 投票，综合向量分数和关键词命中数排序，取 top 5 列。
4. **跨组汇总**：所有关键词组的 top 5 汇总，重复列只保留一份 M-Schema，但标注来源关键词。

**关键设计**：每个关键词独立返回自己的 top 5 列，保留"哪个关键词召回了哪些列"的来源信息。这使下游 Prompt 能清晰说明每列的召回来源，帮助 LLM 理解用户意图与 schema 的对应关系。

**Prompt 呈现策略**：
- **关键词召回映射**：列出每个关键词召回的列，让 LLM 知道列的来源
- **M-Schema**：重复列只出现一次，不重复展示

**示例**：
```
关键词召回映射:
  "学校" → schools.School, schools.CDSCode, schools.City, schools.County, schools.District
  "各科score" → satscores.AvgScrRead, satscores.AvgScrMath, satscores.AvgScrWrite, satscores.NumTstTakr, satscores.enroll12

M-Schema（去重）:
  schools: [School, CDSCode, City, County, District]
  satscores: [AvgScrRead, AvgScrMath, AvgScrWrite, NumTstTakr, enroll12]
```

**理由**：
- 纯向量检索对字面匹配弱——"score" 应直接命中含 "scores" 的文档，但向量空间偏向语义泛化到 "School"。
- 扩大 top_k（5→50）保证相关列进入候选集，再通过 n-gram 投票把真正匹配的列提上来。
- 按关键词分组独立召回，避免不同关键词的召回结果互相干扰（如"学校"的大量召回淹没"score"的结果）。
- 保留来源信息，下游 LLM 能更准确判断用户意图。

**加权公式**：
```
final_score = vector_score × 0.2 + normalized_ngram_vote × 0.8
```
其中 `vector_score = 1.0 - distance`，`normalized_ngram_vote` = 该列的 ngram_vote / 组内最大 ngram_vote。

**参数**：
- `column_top_k_per_keyword = 50`
- 每组返回 top 10 列
- 检索词：keyword phrase + 中文同义词 + 英文同义词（全小写），详见决策 21

---

### 决策 19：列级文档结构 — 全局单 Collection + 精简 document + 全小写

**决策**：所有数据库的列向量存在**同一个** ChromaDB collection（命名 `nl2sql_columns`），不按库拆分。每列一条记录，通过 `metadata.database` 字段区分归属。

**理由**：
- 全量构建只需一次脚本执行，无需逐库维护多个 collection。
- 查询时通过 `where_filter={"database": db_id}` 隔离，效果等同分库。
- 为后续跨库检索或"不指定库名自动判断"留出空间。
- 统一管理，减少维护复杂度。

**记录结构**：

| 字段       | 内容                                                                          |
|------------|-------------------------------------------------------------------------------|
| `id`       | `"{db_id}.{table_name}.{original_column_name}"`                               |
| `document` | embed 用文本（见下方拼装规则），**全小写**                                     |
| `embedding`| BGE-M3 dense 向量（1024 维）                                                  |
| `metadata` | 完整列上下文：database、表名、原列名、人类可读名、数据类型、描述、值描述、格式、PK/FK 标记、引用关系、样本值字符串 |

**document 拼装规则**（精简三段式，`|` 分隔，全小写）：

```
{table_name} | {original_column_name} | {desc}
```

**desc 优先级**：`column_description` → `value_description` → `column_name`（逐级回退，首个非空者）

**全小写原因**：N-gram 匹配需大小写归一化，"score" 与 "Score" 应视为相同子串。

**示例**：
```
satscores | avgscrread | average scores in reading
schools | cdscode | california department schools
satscores | rtype | rtype
```

**metadata 注意事项**：
- ChromaDB metadata value 只允许 str/int/float/bool，列表必须拼成字符串。
- `sample_values` 用 `"|"` 分隔拼接（用于下游展示，不进入 embed 文本，避免与 LSH 值检索重复）。
- `references` 字段格式 `"users.id"`，便于 SS 提示 JOIN 路径。

---

### 决策 20：BGE-M3 本地 CPU 部署 + 一次性离线 Embedding

**决策**：
- BGE-M3 模型部署在本地，使用 CPU 推理（不依赖 GPU，不调远程 embedding API）。
- 对每个数据库执行一次离线脚本，将所有列文档批量 embed 并写入 ChromaDB，持久化到磁盘。
- 运行时只需对 query keyword 做 1 次 embedding（CPU 实测 ~100ms），开销可接受。

**理由**：
- 用户明确"本地只有 CPU，可以先预先 Embedding 好放在向量数据库里"。
- 11 个 BIRD 库列文档总数估计 1500-2000，CPU 一次性 embed 约 3-8 分钟，可接受。
- 完全离线运行，无配额、无网络依赖。

**索引目录布局**（全局统一，不再按库分散）：

```
data/
  └── preprocessed/
       └── chroma/
            └── nl2sql_columns/      ← 全局唯一 collection 持久化目录
```

**强制重建**：构建脚本提供 `force_rebuild=True` 参数；不做基于 sqlite mtime 的自动过期检测（本期）。

---

### 决策 21：检索 query 构造采用「四向同义词扩写 + 每检索词独立检索」

**决策**：列检索时，先通过 LLM 对关键词做四向同义词扩写（中文同义词 + 英文翻译 + 英文同义词 + 中文翻译），再对每个检索词独立发起 `vector_store.query(top_k=50)`，中间不去重。

**四向扩写规则**：
- 中文 phrase → 中文同义词 + 英文翻译
- 英文 phrase → 英文同义词 + 中文翻译
- 所有输出全小写

**LLM 输出格式**：
```json
{
  "keywords": [
    {
      "phrase": "各科score",
      "zh_synonyms": ["各科成绩", "每科分数"],
      "en_synonyms": ["subject score", "course score", "each subject score"]
    }
  ]
}
```

**扁平化后的检索词列表**（全小写）：
```python
query_terms = []
for kw in keywords:
    query_terms.append(kw["phrase"].lower())
    query_terms.extend(s.lower() for s in kw["zh_synonyms"])
    query_terms.extend(s.lower() for s in kw["en_synonyms"])
```

**检索逻辑**：
```python
all_results = []  # 不去重，全部保留
for term in query_terms:
    emb = embed(term)
    hits = chroma.query(emb, n_results=50)
    all_results.extend(hits)
```

**理由**：
- 关键词切太碎会丢失语义（"各科score" 拆成 "各科" + "score" 后语义断裂），保留短语 + 同义词扩写可覆盖更多表达。
- 跨语言扩写解决中文查询 vs 英文描述的语义鸿沟——"成绩" ↔ "score" ↔ "scores"。
- 全小写确保 n-gram 匹配时大小写不干扰。

**短语保留规则**：名词前面的描述性定语、量词等不单独切分。例如"各科score"作为一个整体短语输出，不拆成"各科"和"score"。

---

### 决策 22：全程使用 LangGraph 编排（主图 + 各 Agent 子图）

**决策**：本服务全程基于 LangGraph 进行流程编排，分为两层：

1. **主图（Top-Level Graph）**：编排端到端 NL2SQL 流水线，节点对应各 Agent（2026-06-29 更新，task_planner 前置 + summarize 后置）：
   `history_cache → task_planner → (EXECUTE: run_subqueries | CLARIFY: interrupt | REJECT: END) → aggregate_results → memory_update → End`
   单查询子图内部：`ir → ss → answerability_check → cg → execution → decision`
2. **子图（Sub-Graph）**：每个 Agent 内部的多步功能也用 LangGraph `StateGraph` 串联，避免在 Agent 类里写隐式的命令式控制流。

**主图节点示意**（2026-06-29：task_planner 前置到 IR 之前，新增 summarize）：

```
                    ┌─────┐
                    │START│
                    └──┬──┘
                       ▼
                ┌──────────────┐
                │ history_cache│  ← 决策 30：历史命中检测
                └────┬─────────┘     命中 → 直接执行
                     ▼
              ┌────────────────┐
              │  task_planner  │  ← ★决策 9：意图理解 + 三选一裁决（IR 之前）
              │     _node      │     REJECT → END(拒答)
              └────┬───────────┘     CLARIFY → interrupt 暂停 → resume 回本节点
                   │                  EXECUTE → run_subqueries
                   ▼
            ┌──────────────────┐
            │ run_subqueries   │  ← ★决策 14：单意图直接执行 / 多意图 orchestrator 串行
            │   _node          │     内部复用 build_single_query_graph()
            └────┬─────────────┘     单查询子图: ir→ss→answerability→cg→execution→decision
                 ▼
          ┌────────────────────┐
          │ aggregate_results  │  ← ★决策 15：总结模块（按需 LLM + 数据表结构摘要）
          │      _node         │
          └────┬───────────────┘
               ▼
          ┌──────────────┐
          │ memory_update│  ← 决策 29：记忆自动学习（含反问历史回写）
          └────┬─────────┘
               ▼
            ┌─────┐
            │ END │
            └─────┘

单查询子图 build_single_query_graph() 内部（每个子查询走一遍）：
  ir → ss → answerability_check(false→END拒答) → cg → execution → decision(不可信→拒答)
```

> 注：原 `clarification` 节点（IR 之后）已移除，反问职责前移到 `task_planner`。`answerability_check`（决策 23，SS 之后数据维度层）保留，与 `task_planner`（IR 之前意图层）分层并存。

**典型子图示例：IR Agent 内部**

```
                  ┌────────────────────┐
                  │ extract_keywords   │
                  └──────────┬─────────┘
                             ▼
                  ┌──────────────────┐    ┌──────────────────────┐
                  │ retrieve_values  │    │ retrieve_schema      │
                  │ (LSH + 语义精排) │    │ (per-keyword top-k)  │
                  └──────────┬───────┘    └─────────┬────────────┘
                             └─────────┬───────────┘
                                       ▼
                            ┌──────────────────────┐
                            │ enhance_with_schema  │
                            └──────────────────────┘
```

**典型子图示例：Execution Agent 内部（带错误修正循环）**

```
   ┌─────────┐
   │ explain │
   └────┬────┘
        ▼
   ┌─────────┐
   │ execute │──── success ──► END
   └────┬────┘
        ▼ failed
   ┌──────────┐
   │ llm_fix  │── max_retries reached ──► END
   └────┬─────┘
        └──── 回到 execute（条件边）
```

**理由**：
- 用户明确"本服务全程用 LangGraph 开发，各个 Agent 内也应该用 LangGraph 做功能的串联"。
- LangGraph 提供：
  - **可观察性**：每个节点自动产生 trace，结合 LangSmith 可端到端追踪。
  - **状态管理**：`StateGraph` 显式声明状态字段，避免在 Agent 类里隐藏字段。
  - **条件分支与循环**：原生支持（如 task_planner 反问 interrupt 循环、Execution 修正循环、Decision 全部失败兜底）。
  - **interrupt/resume**：task_planner 的"暂停等用户回答"机制由 LangGraph 1.x `interrupt()` + `Command(resume=...)` + `InMemorySaver` 原生支持（决策 12），无需自己实现。
  - **可插拔**：节点可按配置启用/禁用（如 task_planner 可通过 `config/clarification.yaml` 的 `enabled: false` 关闭，退化为直接 EXECUTE）。

**实现约定**：

| 层级 | 类型 | 状态类型 | 编排器 |
|------|------|----------|--------|
| 主图 | `StateGraph` | `NL2SQLState` (TypedDict) | `src/graph/main_graph.py` |
| 子图 | `StateGraph` | 子 State (TypedDict) 或共用 `NL2SQLState` | 各 Agent 模块的 `agent.py` |

**Agent 类规范**：
- 每个 Agent 类**必须**暴露一个 `build_graph(self) -> CompiledGraph` 方法返回编译后的子图。
- 主图节点函数签名：`def xxx_node(state: NL2SQLState) -> dict` —— 返回需要更新到主 state 的字段。
- 子图与主图通过状态字段交互，不直接持有彼此引用。

**已实现模块的回溯改造**：
- 当前 §2-§7 实现的 IR / SS / CG / Execution / Decision 是命令式调用，本期任务 18.x 会**统一回溯改造为 LangGraph 子图**。
- 改造时保持原有公开 API（如 `InformationRetrieval.retrieve()`）作为子图的对外包装，便于现有测试无感迁移。

**Trade-off**：
- LangGraph 学习成本与样板代码略多于纯函数调用。
- 但收益（监控、可视化、状态显式化、循环/中断原生支持）远大于成本，且符合用户的核心架构要求。

**依赖**：`langgraph>=0.2.0` 已在 §1.5 任务中列入 requirements.txt。

---

### 决策 23：可回答性检查节点（Answerability Check）— SS 后、CG 前

**决策**：在 SS 与 CG 之间新增 `answerability_check` 节点，用 LLM 判断当前 schema 是否有足够信息回答用户问题。

**宽松原则**：宁可放过，不误杀。只有在**明确**缺少关键实体或粒度严重不匹配时才拦截。`uncertain` 一律放行。

**输入**：
- 用户原始问题 (`user_query`)
- SS 输出的 MSchema（表名、列名、数据类型、description、sample_values、PK/FK 关系）
- IR 的 keywords、lsh_hit_count、vector_top_scores

**LLM 返回结构**：
```json
{
  "answerable": "true" | "false" | "uncertain",
  "confidence": 0.0-1.0,
  "reason": "判断理由",
  "missing_info": "缺少什么信息（如果 false/uncertain）",
  "granularity_match": "粒度是否匹配的说明"
}
```

**路由规则**：
- `answerable == "true"` 或 `"uncertain"` → 继续 CG
- `answerable == "false"` → 拒答，将 `reason` 写入 `rejection_reason`，流程跳转到 END

**理由**：
- NL2SQL 系统中 LLM 在信息不足时会"硬凑"——用近似字段替代用户真正要的字段，导致答非所问。
- 提前拦截可省去后续 CG + Exec 阶段的 LLM 调用成本（~30-120s），用一次轻量判断（~2-3s）替代。
- 宽松原则降低误拒风险：`uncertain` 放行是因为 LLM 有时过度谨慎，实际生成 SQL 时可能发现可行方案。

**风险与缓解**：
- **误拒（false negative）**：LLM 判断不可回答但实际可以 → 宽松原则 + uncertain 放行降低此风险。
- **漏判（false positive）**：LLM 判断可回答但实际不行 → 由下游决策 B（结果验证）兜底。

---

### 决策 24：结果可信度验证（Result Verification）— 增强决策节点

**决策**：在决策节点选定最终 SQL 后，增加一步严格的"结果可信度验证"。检查生成的 SQL 是否真正在回答用户的问题，而非答非所问。

**严格原则**：宁可多拒，不放过答非所问。这是最后一道防线。

**输入**：
- 用户原始问题 (`user_query`)
- 最终选定的 SQL (`selected_sql`)
- SQL 执行结果的列名 + 前 5 行样例
- 原始 MSchema（用于对照）

**LLM 检查维度**：
1. **粒度匹配**：SQL 查询的粒度是否与问题匹配（如问"每个学生"但 SQL 查的是"每个学校"）
2. **维度覆盖**：结果列是否覆盖了问题中请求的维度（如问"姓名+分数"但结果只有分数）
3. **硬凑检测**：是否存在用近似字段替代了用户真正要的字段（如用"School"替代"学生姓名"）

**LLM 返回结构**：
```json
{
  "trustworthy": "true" | "false",
  "reason": "验证理由",
  "granularity_match": "粒度对齐说明",
  "semantic_alignment": "语义对齐说明"
}
```

**路由规则**：
- `trustworthy == "true"` → 正常返回结果
- `trustworthy == "false"` → 拒答，将 `reason` 写入 `rejection_reason`，流程跳转到 END

**理由**：
- 方案 A（可回答性检查）宽松放行后，部分"看起来能答但实际答非所问"的情况需要兜底。
- 这是用户能看到的最后一道关卡，必须严格。答非所问比拒答对用户伤害更大——拒答至少诚实，答非所问会误导。
- 基于实际执行结果判断，信息比方案 A 更充分，准确率更高。

**与方案 A 的关系**：
- A 是"快筛"：低成本早期拦截明显的不可回答
- B 是"精验"：高成本严格验证，兜底 A 漏过的情况
- 两者互补，形成双重保障

---

### 决策 25：N-gram 投票精排 — 按关键词组内独立投票

**决策**：在向量粗召回之后，按原生关键词分组进行 3-gram 子串匹配投票，每组独立排序返回 top 5。

**问题背景**：纯向量检索对字面匹配弱——"score" 查询时，`schools | school | school` 的向量距离（0.405）反而比 `satscores | avgscrmath | average scores in math`（0.440）更近，因为短文档向量更集中。但 "score" 应直接命中含 "scores" 的文档。

**分组投票逻辑**：

```python
for keyword_group in keyword_groups:
    # keyword_group = {"phrase": "各科score", "terms": ["各科score", "各科成绩", "subject score", ...]}
    
    # 1. 该组所有 terms 各自查 top50，取并集
    candidates = union_of_all_term_results(keyword_group["terms"])
    
    # 2. 组内 N-gram 投票（只用本组的 terms）
    for candidate in candidates:
        vote = ngram_vote_score(candidate.document, keyword_group["terms"], n=3)
        final_score = candidate.vector_score * 0.2 + normalized_vote * 0.8
    
    # 3. 该组 top 5 列
    group_top5 = sorted(candidates, by=final_score)[:5]
```

**N-gram 投票函数**：

```python
def ngram_vote_score(document: str, query_terms: list, n: int = 3) -> float:
    """
    只对 query_terms 做 n-gram 拆解，在 document 原文中统计每个 n-gram 的出现次数。
    document 不拆解，避免 '|' 分隔符产生噪声 n-gram。

    计分方式：累加所有 term 的所有 n-gram 在 document 中的出现次数。
    例如 "school" 的 "sch" 在 document 中出现 2 次 → 贡献 2 分。
    """
    total_hits = 0
    for term in query_terms:
        term_ngrams = _char_ngrams(term, n)
        for ng in term_ngrams:
            total_hits += document.count(ng)
    return float(total_hits)
```

**不除以 terms 总数**：当前架构按关键词分组独立召回，组内排序时除以常数不影响排序结果，跨组汇总时也不做排名比较，因此无需归一化。

**综合排序**：
```python
final_score = vector_score * 0.2 + normalized_ngram_vote * 0.8
```

- `vector_score = 1.0 - distance`
- `normalized_ngram_vote = ngram_vote / max_ngram_vote_in_group`
- 向量相似度权重不超过 0.2，关键词匹配为主导信号

**跨组汇总**：
- 所有组的 top 5 汇总
- 重复列（被多个关键词召回）只保留一份 M-Schema，但标注来源关键词
- Prompt 中同时展示"关键词→列"映射关系和去重后的 M-Schema

**示例**：

```
查询: "各个学校的各科score"
关键词组:
  "学校" → terms: [学校, 院校, school, schools]
  "各科score" → terms: [各科score, 各科成绩, subject score, course score]

组1 "学校" 的 top5:
  schools.School, schools.CDSCode, schools.City, schools.County, schools.District

组2 "各科score" 的 top5:
  satscores.AvgScrRead, satscores.AvgScrMath, satscores.AvgScrWrite,
  satscores.NumTstTakr, satscores.enroll12

→ 无重复列，两组结果直接合并
→ Prompt 中分别标注来源关键词
```

**理由**：
- 按关键词分组避免"学校"的大量召回淹没"score"的结果——这正是之前全局 top5 方案的核心问题。
- 组内投票只用本组同义词，投票信号更精准（"school"不会干扰"score"组的投票）。
- 每个关键词独立返回 top 5，保留来源信息，下游 LLM 能更好理解用户意图与 schema 的对应关系。
- 全小写 + n-gram 归一化，避免大小写差异导致漏匹配。

---

### 决策 26：表关联图（Schema Relationship Graph）— 预处理阶段构建 + 召回时注入 JOIN 路径

**决策**：在预处理阶段为每个数据库构建表关联图（JSON 邻接表），包含显式 FK 和隐式关联（向量相似度匹配 + 值命中率检测 + LLM 辅助）。IR 召回后，根据召回的表集合从图中提取 JOIN 路径和连接键，注入 Prompt。

**问题背景**：IR 召回了多个表的列，但 LLM 不知道表之间如何关联。例如 `satscores.cds = schools.CDSCode` 这个 JOIN 条件，Prompt 里没有体现，LLM 可能生成错误的 JOIN 或直接笛卡尔积。

**隐式关联检测流水线**：

```
Stage 1: 显式 FK（已有）
  PRAGMA foreign_key_list → 直接提取

Stage 2: 向量相似度匹配 + 值命中率检测
  对每对未被 Stage 1 连接的表:
  1. 利用已有 ChromaDB 向量库，计算跨表列的向量余弦相似度
     - 每个列的 document 已有 embedding，无需重新编码
     - 对表 A 的每个列，在表 B 的所有列中检索最相似的列
  2. 取相似度最高的 top 3 列对作为候选
  3. 对每个候选列对做值命中率检测：
     - 从表 A 的列取 N 个 DISTINCT 值（默认 20）
     - 检查这些值在表 B 的列中有多少能匹配到
     - 命中率 = 匹配数 / 样本数
     - 命中率超过阈值（默认 0.5）→ 确认为隐式关联，输出为 join_key
  4. 支持多连接键：两个表间可存在多个 join_key
     （如同时有 school_id 和 school_name 都匹配）

Stage 3: LLM 辅助（覆盖死角）
  对 Stage 1-2 都没发现关联的孤立表:
  - 把两表的 schema + sample_values 喂给 LLM
  - 让 LLM 判断是否可能存在 JOIN 关系
  - LLM 返回的 join_keys 也需要经过命中率检测验证（防止 LLM 幻觉）
  - 成本高但能发现规则漏掉的关系
```

**为什么用命中率而非 Jaccard**：
- Jaccard 要求双方各自取样的值有交集，对大表而言两边各取 20 个值交集概率极低
- 命中率只需验证"表 A 的值在表 B 中是否存在"，即使表 B 有百万行也能准确判断
- SQL 实现：`SELECT COUNT(*) FROM (SELECT DISTINCT col_a FROM table_a LIMIT N) WHERE col_a IN (SELECT DISTINCT col_b FROM table_b)`

**存储结构（JSON 邻接表）**：

```json
{
  "california_schools": {
    "nodes": {
      "schools": {"columns": ["CDSCode", "School", ...]},
      "satscores": {"columns": ["cds", "AvgScrRead", ...]},
      "frpm": {"columns": ["CDSCode", "SchoolName", ...]}
    },
    "edges": [
      {
        "from": "satscores",
        "to": "schools",
        "join_keys": [["satscores.cds", "schools.CDSCode"]],
        "type": "explicit_fk"
      },
      {
        "from": "frpm",
        "to": "schools",
        "join_keys": [
          ["frpm.CDSCode", "schools.CDSCode"],
          ["frpm.SchoolName", "schools.School"]
        ],
        "type": "vector_similarity"
      }
    ]
  }
}
```

**edge.type 取值**：
- `explicit_fk`：PRAGMA 外键
- `vector_similarity`：向量匹配 + 命中率检测通过
- `llm_inferred`：LLM 辅助推断 + 命中率检测通过

**运行时 JOIN 路径提取**：
1. IR 召回后，收集所有涉及的表名
2. 在图上对这些表做 BFS，找两两之间的**最短路径**（多条路径时取最短）
3. 提取路径上的 edge 及 join_keys
4. 识别桥接表（路径中出现但不在 IR 召回表集合中的表）
5. 桥接表的 M-Schema 自动补充到 `RetrievedContext` 中
6. 格式化为 Prompt 片段

**桥接表处理**：当两个 IR 召回的表之间没有直接边，需要经过第三个表才能 JOIN 时，该中间表为桥接表。桥接表虽然未被关键词召回，但 JOIN 必须依赖它，因此需要：
- 从向量库中查询桥接表的所有列，补充到 `RetrievedContext.columns`
- 补充桥接表到 `RetrievedContext.tables`
- Prompt 中标注桥接表

**Prompt 注入示例**：
```
表关联:
  schools ←[satscores.cds = schools.CDSCode]→ satscores
  schools ←[frpm.CDSCode = schools.CDSCode, frpm.SchoolName = schools.School]→ frpm

JOIN 条件:
  satscores JOIN schools ON satscores.cds = schools.CDSCode
  frpm JOIN schools ON frpm.CDSCode = schools.CDSCode AND frpm.SchoolName = schools.School

桥接表: schools
```

**存储位置**：`data/preprocessed/schema_graphs/{db_id}.json`

---

### 决策 27：预处理增量更新 — Manifest 快照对比 + 按依赖顺序增量重建

**决策**：为三个预处理模块（Schema Index / Schema Graph / LSH Index）提供统一的增量更新能力，通过 Manifest 快照对比检测 schema 变更，按依赖顺序执行增量更新。

**问题背景**：当前三个预处理模块只支持全量重建。当数据库发生 DDL 变更（新增/删除表、增删列、修改列类型）时，必须重跑全量构建脚本，耗时长且浪费计算。实际场景中数据库变更较频繁，需要增量更新能力。

**Manifest 快照**：

存储位置：`data/preprocessed/manifest.json`

```json
{
  "version": 1,
  "last_updated": "2026-06-05T10:30:00",
  "databases": {
    "california_schools": {
      "schema_index_build_time": "2026-06-05T10:00:00",
      "schema_graph_build_time": "2026-06-05T10:05:00",
      "lsh_index_build_time": null,
      "tables": {
        "schools": {
          "columns": {
            "SchoolId": {"type": "INTEGER", "is_fk": false},
            "CDSCode": {"type": "TEXT", "is_fk": true, "references": "satscores.cds"}
          }
        },
        "satscores": {
          "columns": {
            "cds": {"type": "TEXT", "is_fk": false}
          }
        }
      }
    }
  }
}
```

columns 使用对象（而非列表）存储，方便按列名快速 diff。全量构建完成后自动写入 Manifest，增量更新以此为基准。

**三模块独立 build_time**：每个预处理模块有独立的 `build_time`（`schema_index_build_time` / `schema_graph_build_time` / `lsh_index_build_time`），各构建脚本只写自己的时间戳。`null` 表示该模块尚未构建。增量更新时可根据各模块的 `build_time` 判断是否需要全量构建。

**依赖顺序与级联触发**：

```
① Schema Index (ChromaDB)  ← 必须先执行，Schema Graph Stage 2 依赖列向量
② Schema Graph (JSON)      ← 依赖 Schema Index
③ LSH Index (Pickle)       ← 独立，但逻辑上在 schema 稳定后执行
```

任何一步失败则停止，保持一致性。

关键依赖规则：
- Schema Graph 依赖 Schema Index：如果 Schema Index 尚未构建（`build_time == null`），Schema Graph 不能运行，必须先构建 Schema Index
- Schema Index 发生变更 → Schema Graph 需要重新处理（即使自身 diff 为空，因为依赖的向量已变）
- LSH Index 独立：不受其他模块变更影响，仅根据自身 diff 决定是否更新

各模块增量判断逻辑：

| 模块 | build_time == null | 上游有变更 | diff 有变更 | 动作 |
|------|-------------------|-----------|------------|------|
| Schema Index | 是 | — | — | 全量构建 |
| Schema Index | 否 | — | 是 | 增量更新 |
| Schema Index | 否 | — | 否 | 跳过 |
| Schema Graph | 是 | — | — | 全量构建（前提：Schema Index 已构建） |
| Schema Graph | 是 | — | — | 跳过 + 警告（Schema Index 未构建） |
| Schema Graph | 否 | 是（Index 变了） | — | 重新处理 |
| Schema Graph | 否 | 否 | 是 | 增量更新 |
| Schema Graph | 否 | 否 | 否 | 跳过 |
| LSH Index | 是 | — | — | 全量构建 |
| LSH Index | 否 | — | 是 | 增量更新 |
| LSH Index | 否 | — | 否 | 跳过 |

**Diff → Action 映射**：

| 变更类型 | Schema Index | Schema Graph | LSH Index |
|----------|-------------|--------------|-----------|
| 新增表 T | upsert T 所有列 | T vs 所有表做 Stage 1/2/3 | 重建 T 的 MinHash |
| 删除表 T | delete T 所有列 | 删 T 的 node + 相关边 | 删除 T 的所有 key |
| 表 T 新增列 | upsert 单列 | 只对 T 与未连接表做 S2 匹配 | TEXT 列则加入 MinHash |
| 表 T 删除列 | delete 单列 | 清理含该列的 join_key；边保留（≥1 个 join_key 即存续） | 移除该列的 key |
| 表 T 修改列类型 | upsert 覆盖 | 重验证受影响的 join_key 类型兼容性 | 值变化则重建列 |

**Schema Graph 增量核心逻辑**：

- **已有连接的表**：新增列不影响已有关系，跳过
- **未连接的表**：新增列可能带来新的连接机会，只拿新增列的向量去 ChromaDB 匹配
- **删除列**：检查该列是否参与了 join_key，是则移除该 join_key；边至少保留一个 join_key 即可存续

**LSH Index 增量策略**：表级重建（而非行级修改）

- 原因：MinHashLSH 的 pickle 序列化对大索引开销 ≈ 重建；单表 MinHash 计算通常几秒
- 新增表 → 重建该表的 MinHash，insert 到 LSH
- 删除表 → 从 LSH 中 remove 该表所有 key
- 修改表 → 先 remove 旧 key，再 insert 新 key

**统一入口**：

```python
from src.preprocessing.incremental_updater import IncrementalUpdater

updater = IncrementalUpdater(data_dir="data")
report = updater.update(db_id="california_schools")

# 全量扫描所有库
reports = updater.update_all()
```

**遗留项**：

- 列值的变化不体现在 Manifest 中（只记录 schema 级信息）。LSH 索引和命中率验证的缓存结果可能因此过期，需通过定期全量重建兜底。未来可考虑记录行数/count hash 等轻量指纹做值级变化检测。

---

### 决策 28：会话记忆 — 持久化、按用户隔离、注入 Prompt

**决策**：会话记忆持久化到 `data/sessions/{user_id}/{session_id}.json`，一个用户可有多个会话，会话不跨用户。会话内多轮对话历史作为 Prompt 上下文注入后续查询。

**会话数据结构**：

```json
{
  "session_id": "uuid-aaa",
  "user_id": "alice",
  "created_at": "2026-06-05T10:00:00",
  "updated_at": "2026-06-05T15:30:00",
  "status": "active",
  "conversation_history": [
    {
      "turn_index": 1,
      "user_query": "查一下苹果的销售额",
      "resolved_keywords": ["苹果", "销售额", "gmv"],
      "final_sql": "SELECT SUM(gmv) FROM sales WHERE product='Apple'",
      "final_result_sample": [{"gmv_total": 10000}],
      "tables_used": ["sales"],
      "timestamp": "2026-06-05T10:05:00"
    }
  ],
  "context_summary": {
    "last_topic": "苹果销售额查询",
    "last_tables": ["sales"],
    "last_time_range": "2025"
  }
}
```

**与 LangGraph 的集成**：
- 每次调用 `graph.invoke()` 前，从 SessionMemory 中提取最近 N 轮对话历史，写入 `NL2SQLState.conversation_history`
- IR 节点读取 `conversation_history`，辅助 follow-up 查询的关键词提取和上下文理解
- 主图执行完毕后，提取本轮结果写入 SessionMemory

**存储策略**：
- 持久化到 JSON 文件，`data/sessions/{user_id}/{session_id}.json`
- 同时在内存中保持 LRU 缓存（热会话加速）
- 会话不设自动过期，用户可手动删除

**理由**：
- 会话是用户与系统的连续对话上下文，必须持久化以支持断线恢复和多轮对话
- 按用户分目录存储，天然隔离，无需额外权限控制
- 会话记忆作为 Prompt 注入而非修改图结构，保持 LangGraph invoke 的纯函数特性

---

### 决策 29：用户记忆扩展 — 6 维长期记忆 + 自动学习

**决策**：用户长期记忆从 2 维（术语偏好 + 澄清历史）扩展为 6 维，新增常用表、指标定义、查询偏好、领域上下文。

**完整用户记忆结构**：

```json
{
  "user_id": "alice",
  "created_at": "...",
  "updated_at": "...",

  "term_preferences": {
    "销售额": {"resolved_to": "gmv", "confidence": 0.9, "source": "user_taught", "last_used": "..."}
  },

  "frequently_used_tables": {
    "sales": {"query_count": 23, "last_used": "..."},
    "orders": {"query_count": 15, "last_used": "..."}
  },

  "metric_definitions": {
    "GMV": {
      "description": "完成订单金额总和",
      "sql_pattern": "SUM(order_amount) WHERE status='completed'",
      "source": "auto_learned",
      "confidence": 0.7,
      "times_used": 3,
      "last_used": "..."
    }
  },

  "query_preferences": {
    "default_time_range": "last_30_days",
    "default_group_by": "daily",
    "default_sort": "DESC",
    "default_limit": 10
  },

  "domain_context": {
    "industry": "生鲜电商",
    "department": "运营部",
    "focus_areas": ["销售分析", "用户增长"]
  },

  "clarification_history": [...]
}
```

**各维度来源与用途**：

| 维度 | 来源 | 下游用途 |
|------|------|----------|
| term_preferences | 用户澄清 / 主动教 | TaskPlanner 歧义消解、IR 关键词替换 |
| frequently_used_tables | 自动学习（从 SQL 提取表名） | IR 召回加权、SS 优先保留 |
| metric_definitions | auto_learned + user_taught | CG 注入已知指标、历史命中检测 |
| query_preferences | 自动学习（统计频率） | CG 注入默认参数（时间/排序/limit） |
| domain_context | 从查询中推断 / 用户主动设定 | IR 关键词扩展、CG 生成策略 |
| clarification_history | 反问流程写入 | TaskPlanner 反问上下文 |

**metric_definitions 学习机制**：
- `auto_learned`：每次查询完成后，如果 SQL 是简单聚合（SUM/COUNT/AVG + WHERE），调 LLM 提取"指标名 → SQL 模式"映射，confidence 从低开始（0.5），多次使用相同模式则递增
- `user_taught`：用户在反问/澄清中主动说明指标含义，confidence=0.95
- 自动学习的指标 confidence < 0.8 时不在 Prompt 中主动推荐，仅在历史命中检测中使用

**记忆学习时机**：主图新增 `memory_update` 节点，在 decision 之后执行，自动提取本轮结果写入 UserMemory。

**理由**：
- 用户长期记忆的核心价值是"让系统越用越懂你"
- 6 个维度覆盖从术语映射到查询习惯的完整偏好谱系
- 自动学习降低用户主动教的负担，但用户教的置信度始终最高

---

### 决策 30：历史命中检测 — 复用 SQL 重新执行

**决策**：在 IR 之前新增 `history_cache` 节点，通过 LLM 判断当前查询是否与历史查询等价或可用已知指标直接回答。命中时复用历史 SQL 重新执行，不复用历史 result。

**处理流程**：

```
1. 从 SessionMemory 取最近 N 轮对话
2. 从 UserMemory 取 metric_definitions
3. 调 LLM 判断：
   - 当前问题是否和历史上某轮完全相同/等价？
   - 或者当前问题是否可以用已知指标定义直接回答？
4. 输出：{can_reuse: bool, source: str, cached_sql: Optional[str], confidence: float}
```

**安全边界**：
- confidence < 0.8 → 不复用，走完整链路
- 涉及时间变化的 follow-up（如"昨天的"→"今天的"）不复用，因为数据可能已变
- 只复用 SQL 不复用 result——数据可能已更新，重新执行保证结果时效性
- 缓存命中时跳过 IR/SS/CG，直接走 Execution + Decision

**理由**：
- 用户反复问相同或等价问题是常见场景（"今天销售额" / "现在的销售额"）
- 复用 SQL 重新执行既节省 IR+SS+CG 的 LLM 调用成本（~20-60s），又保证数据时效性
- 不复用 result 是因为数据可能已变化，特别是时序类查询

---

### 决策 31：问数服务 API — FastAPI + SSE 流式

**决策**：基于 FastAPI 提供问数服务 HTTP API，SSE 流式输出每个阶段的中间状态，避免用户在等待时无反馈。

**API 设计**：

```
POST /api/v1/query              # 核心查询接口（SSE 流式）
GET  /api/v1/sessions/{user_id} # 列出用户的所有会话
GET  /api/v1/sessions/{session_id}/history  # 获取会话对话历史
DELETE /api/v1/sessions/{session_id}         # 删除会话
GET  /api/v1/users/{user_id}/memory          # 获取用户长期记忆
GET  /api/v1/users/{user_id}/metrics         # 获取用户的指标定义
GET  /api/v1/health                           # 健康检查
```

**POST /api/v1/query 请求**：

```json
{
  "query": "查一下苹果的销售额",
  "session_id": "uuid-aaa",
  "user_id": "alice"
}
```

**SSE 流式响应事件序列**：

```
data: {"type": "cache_check", "hit": false}

data: {"type": "stage", "stage": "ir", "status": "started"}
data: {"type": "stage", "stage": "ir", "status": "completed", "keywords": ["苹果","销售额"]}

data: {"type": "stage", "stage": "ss", "status": "started"}
data: {"type": "stage", "stage": "ss", "status": "completed"}

data: {"type": "stage", "stage": "cg", "status": "started"}
data: {"type": "stage", "stage": "cg", "status": "completed", "sql_count": 3}

data: {"type": "stage", "stage": "execution", "status": "started"}
data: {"type": "stage", "stage": "execution", "status": "completed"}

data: {"type": "clarification", "question": "...", "options": [...], "trigger_type": "..."}

data: {"type": "result", "session_id": "uuid-aaa", "turn_index": 3,
       "final_sql": "SELECT ...", "final_result": [...], "execution_time_ms": 2340}

data: {"type": "done"}
```

**缓存命中时**：

```
data: {"type": "cache_check", "hit": true, "source": "session_history", "confidence": 0.95}
data: {"type": "result", "final_sql": "SELECT ...", "from_cache": true}
data: {"type": "done"}
```

**服务架构**：

- 启动时一次性加载所有组件（DatabaseConnector、LSHIndexer、VectorStoreManager、LLMClient、各 Agent）
- 会话管理器：内存 LRU 缓存 + 持久化 JSON
- 用户记忆管理器：LRU 缓存（最多 100 个用户），读时加载、写时持久化
- API 认证本期不做，代码中标注 TODO

**理由**：
- SSE 流式让前端能实时展示各阶段状态（"正在检索..."、"正在生成 SQL..."），避免用户在傻等
- FastAPI 原生 async + Pydantic 校验 + 自动文档，是 Python API 服务的最佳实践
- 会话和用户记忆的管理与 API 层绑定，不在 LangGraph 图内处理

---

### 决策 32：主图新增 history_cache + memory_update 节点

**决策**：主图在现有流程基础上新增两个节点，调整后的完整流程为：

```
START → history_cache → (命中→execution→decision→memory_update→END)
              ↓未命中
              ir → clarification → ss → answerability_check → cg → execution → decision → memory_update → END
```

**history_cache 节点**：
- 位置：START 之后，IR 之前
- 功能：检测历史命中，命中时直接复用 SQL 跳到 execution
- 条件边：`cache_hit == True` → execution；否则 → ir

**memory_update 节点**：
- 位置：decision 之后，END 之前
- 功能：提取本轮结果，更新 SessionMemory（追加 Turn）和 UserMemory（自动学习）
- 条件边：无条件 → END

**NL2SQLState 新增字段**：

```python
conversation_history: List[Dict[str, Any]]  # 会话历史（由 API 层注入）
cache_hit: bool                             # 历史命中标记
cached_sql: Optional[str]                   # 命中的缓存 SQL
cache_source: Optional[str]                 # 命中来源
cache_confidence: float                     # 命中置信度
```

**理由**：
- history_cache 前置可节省大量 LLM 调用成本
- memory_update 后置确保所有结果都已确定后才学习
- 会话历史通过 state 注入而非在图内管理，保持图的无副作用特性

---

## TaskPlanner 三选一裁决规约（2026-06-29 重写，替代原四类触发）

原「触发条件 A/B/C/D」基于 IR 召回结果，已废弃。新方案由 `TaskPlanner` 在 IR 之前做三选一裁决：

| Verdict | 名称 | 检测逻辑 | 流向 |
|---------|------|----------|------|
| EXECUTE (single) | 清晰单意图 | 实体、度量、维度、时间均明确 | 单查询子图直接执行 |
| EXECUTE (multi) | 清晰多意图 | 含多个独立问数意图，可分解 | 分解为 N 子查询逐个执行 |
| CLARIFY | 表述歧义 | 实体多义 / 粒度不明 / 缺失关键限定 | interrupt 暂停等用户澄清 |
| REJECT | 拒答 | 越权写操作 / 超出数据范围 / 无法理解 | 直接 END 带拒答原因 |

**裁决由 LLM 强制 JSON 输出完成**，解析失败降级为 EXECUTE 单意图（不阻塞主流程）。

**反问上限**：5 轮，计数器 `clarify_round` 存 state（checkpoint 持久化）。达上限降级 EXECUTE 最佳猜测或 REJECT。拒答关键词（"不知道/跳过/算了/skip/不清楚/随便"）识别后立即放行。

**与 answerability_check 分层**：TaskPlanner（IR 前）拦截意图层歧义；answerability_check（SS 后，决策 23）拦截数据维度层不可答。并存不替代。

---

## 模块结构（反问相关，2026-06-29 重写）

```
src/
├── clarification/
│   ├── __init__.py
│   ├── task_planner.py          # ★核心：意图理解 + 三选一裁决 + 多意图分解 + 反问问题生成
│   ├── dialog.py                # interrupt 包装 + 5次硬上限 + 拒答关键词识别（决策 13）
│   ├── subquery_orchestrator.py # 多子查询串行编排 + 失败隔离（决策 14）
│   ├── result_summarizer.py     # ★总结模块：按需 LLM 汇总 + 数据表结构摘要降 token（决策 15）
│   └── agent.py                 # 反问子图组装（task_planner + dialog 组合，原 §14）
├── graph/
│   ├── state.py                 # NL2SQLState（新增 plan_result/subqueries/subquery_results/clarify_round 等）
│   ├── main_graph.py            # 主图（task_planner 前置 + checkpointer + interrupt）
│   └── single_query_graph.py    # ★单查询子图工厂 build_single_query_graph()（ir→ss→answerability→cg→execution→decision）
├── verification/
│   ├── __init__.py
│   ├── answerability.py         # AnswerabilityChecker：可回答性检查（决策 23，IR 后数据维度层）
│   └── result_verifier.py       # ResultVerifier：结果可信度验证（决策 24）
├── memory/
│   ├── __init__.py
│   ├── user_memory.py           # UserMemory 主类（6 维长期记忆，决策 29）
│   ├── session_memory.py        # SessionMemory 会话记忆（决策 28）
│   ├── session_manager.py       # SessionManager 会话生命周期管理
│   ├── history_cache.py         # HistoryCache 历史命中检测（决策 30）
│   ├── memory_updater.py        # MemoryUpdater 自动学习模块（决策 29）
│   └── storage.py               # JSON 文件读写 + 文件锁 + 原子写入
└── api/
    ├── __init__.py
    ├── app.py                   # FastAPI 应用 + 生命周期
    ├── routes/
    │   ├── __init__.py
    │   ├── query.py             # POST /query（SSE 流式，决策 31）+ resume 支持
    │   ├── session.py           # 会话 CRUD
    │   └── user.py              # 用户记忆查询
    ├── schemas.py               # Pydantic 请求/响应模型（新增 resume 字段）
    ├── deps.py                  # 依赖注入
    └── stream.py                # SSE 事件生成器（新增 clarification 事件 + interrupt 检测）

# 已废弃（本期跳过）：web_search.py（Tavily）、trigger.py（IR 后 TriggerDetector 四类触发）、question_generator.py（并入 task_planner）
```

---

## LangGraph 集成示意（2026-06-29 重写：task_planner 前置 + interrupt + checkpointer）

```python
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.memory import InMemorySaver

graph = StateGraph(NL2SQLState)

# 节点
graph.add_node("history_cache", history_cache_node)
graph.add_node("task_planner", task_planner_node)          # ★新增：IR 前意图理解
graph.add_node("run_subqueries", run_subqueries_node)      # ★新增：单/多子查询编排
graph.add_node("aggregate_results", aggregate_results_node)# ★新增：总结模块
graph.add_node("memory_update", memory_update_node)

# 入口 → history_cache → task_planner
graph.add_edge(START, "history_cache")

# history_cache 命中 → 跳过 planner 直接执行
graph.add_conditional_edges(
    "history_cache",
    lambda s: "execution_single" if s.get("cache_hit") else "task_planner",
)

# task_planner 三选一：REJECT → END；CLARIFY → interrupt（恢复后回 task_planner）；EXECUTE → run_subqueries
graph.add_conditional_edges(
    "task_planner",
    route_after_planner,  # {"run_subqueries":..., "task_planner":...(clarify 循环), END: ...(reject)}
)

# task_planner 节点内部（verdict=clarify 且未达上限时）：
def task_planner_node(state):
    plan = planner.plan(state["user_query"], state.get("conversation_history"), state.get("database_filter"))
    if plan.verdict == "clarify" and state.get("clarify_round", 0) < 5:
        # interrupt 暂停：value 送给前端，resume 时返回用户回答
        answer = interrupt({"question": plan.clarify_question,
                            "ambiguities": plan.ambiguities,
                            "round": state.get("clarify_round", 0) + 1})
        # 用户回答后重新规划（带澄清上下文）
        plan = planner.plan(state["user_query"], ..., clarified=answer)
    return {"plan_result": plan.to_dict(), "subqueries": plan.subqueries, ...}

# run_subqueries：单意图直接调单查询子图；多意图 orchestrator 串行调用
# aggregate_results：单结果无表透传；多结果/有表调 LLM 汇总（数据表用结构摘要）
graph.add_edge("run_subqueries", "aggregate_results")
graph.add_edge("aggregate_results", "memory_update")
graph.add_edge("memory_update", END)

# ★必须配 checkpointer 才能恢复 interrupt
graph = graph.compile(checkpointer=InMemorySaver()).with_config(run_name="nl2sql-pipeline")

# 首次执行
config = {"configurable": {"thread_id": session_id}}
for chunk in graph.stream(initial_state, config, stream_mode="updates"):
    if "__interrupt__" in chunk:   # 反问暂停
        emit clarification 事件；当前 SSE 流 done
        break

# 用户回答后 resume（同一 thread_id）
for chunk in graph.stream(Command(resume=user_answer), config, stream_mode="updates"):
    ...  # 从中断点恢复
```

`NL2SQLState` 需新增字段（2026-06-29）：
```python
class NL2SQLState(TypedDict):
    # ... existing fields ...
    plan_result: Dict[str, Any]              # TaskPlanner 输出（verdict/subqueries/ambiguities...）
    subqueries: List[str]                    # 分解后的子查询列表
    subquery_results: List[Dict[str, Any]]   # 每个子查询的最终结果
    clarify_round: int                       # 反问轮次计数（checkpoint 持久化）
    clarify_question: str                    # 当前要问用户的问题（interrupt 用）
    summary_text: str                        # 总结模块输出
    # 保留：clarification_history / clarified_keywords / clarification_done（语义微调）
    # 保留：answerability_result / result_verification / rejection_reason
```


---

## 测试策略（反问相关，2026-06-29 重写）

| 测试类型 | 覆盖场景 |
|----------|----------|
| 单元测试 | `TaskPlanner.plan()` 三选一裁决（EXECUTE单/EXECUTE多/CLARIFY/REJECT）；多意图分解；`DialogManager` 拒答关键词识别 + 5 次上限；`UserMemory` CRUD；JSON 文件锁并发 |
| Mock 测试 | LLM 解析失败降级 EXECUTE；interrupt 首次/resume 行为；LLM 超时；用户拒答；多子查询某条失败不中断其他 |
| 集成测试 | `SubqueryOrchestrator` 多子查询串行 + 失败隔离；`aggregate_results` 按需 LLM + 数据表结构摘要 |
| interrupt 测试 | `InMemorySaver` checkpointer 配置；首次 stream 检测 `__interrupt__`；`Command(resume=...)` 恢复；同 thread_id 多轮反问状态共享；5 次上限强制退出 |
| 端到端 | LangGraph 完整运行含反问的查询（首问→interrupt→resume→执行→总结→memory_update）；MemoryWriter 正确写入反问历史 |

---

## Risks / Trade-offs

**已知风险**:
1. **数据库格式兼容性**: BIRD-SQL数据集可能包含不同格式的数据库文件，需要灵活的连接策略
2. **LLM API成本**: 多候选生成和错误修正会增加API调用次数，影响成本
3. **执行安全性**: 尽管有安全验证，仍需确保不会执行危险操作
4. **性能瓶颈**: 向量检索和多SQL执行可能影响响应时间

**权衡考虑**:
1. **准确性 vs 性能**: 多候选生成提高准确性但增加延迟，通过限制候选数量(5个)平衡
2. **功能完整性 vs 开发复杂度**: 专注于核心NL2SQL功能，暂不实现高级特性
3. **本地开发 vs 生产部署**: 优先保证本地开发体验，后续再考虑生产优化

**反问 Agent 相关风险与缓解（2026-06-29 重写）**:

| 风险 | 缓解方案 |
|------|----------|
| 用户被反问烦扰 | 5 次硬上限 + 拒答关键词识别（可配置） |
| 记忆文件损坏 | 写入采用「先写临时文件 + 原子 rename」+ 备份上次版本 |
| TaskPlanner LLM 误判 verdict | 解析失败降级 EXECUTE 单意图（不阻塞主流程）；可通过 `config/clarification.yaml` 的 `enabled: false` 整体关闭 planner |
| interrupt 恢复失败（thread_id 不匹配/checkpointer 丢失） | InMemorySaver 进程内有效；thread_id 严格用 session_id；恢复前用 `graph.get_state(config)` 校验状态存在 |
| InMemorySaver 进程重启丢失反问状态 | 本期接受（用户确认 MemorySaver）；生产化时换 PostgresSaver |
| 多意图分解过度（把完整意图拆碎） | Prompt 约束"保持语义完整"；单意图不分解 |
| 总结模块对大表 token 爆炸 | 数据表只取「列名+行数+前5行」结构摘要喂 LLM，原始结果透传前端 |
| TaskPlanner 与 answerability_check 职责重叠 | 分层明确：planner 拦意图层（IR前），answerability 拦数据层（SS后），并存不替代 |

**可回答性验证相关风险与缓解**：

| 风险 | 缓解方案 |
|------|----------|
| 方案 A 误拒（false negative） | 宽松原则 + uncertain 放行；可配置 `answerability_strictness` 控制阈值 |
| 方案 A 漏判（false positive） | 由方案 B（结果验证）兜底，双重保障 |
| 方案 B 过严导致正常查询被拒 | Prompt 中强调"只有明确答非所问才判不可信"；可通过配置 `verification_strictness` 调整 |
| 两次 LLM 调用增加延迟 | 方案 A 拦截时反而省时间（~2-3s vs ~30-120s）；方案 B 只在最终决策后调用一次 |
| 拒答用户体验差 | 拒答时返回详细原因 + 缺失信息说明，让用户知道为什么无法回答 |

---

### 决策 49：API 多数据库分池 — DbContextPool（LRU）+ 全局共享单例

**背景与问题**

当前 API 实现（`src/api/deps.py:init_components` + `run_api.py:bootstrap`）存在以下问题：

1. **数据库写死**：服务启动时绑定单一 `db_id`，整个进程只能服务一个数据库；换库必须重启服务。
2. **schema 范围不可控**：`QueryRequest` 缺少 `db_id` 字段，请求层面无法选库。
3. **记忆未按场景隔离召回**：当前默认 `metric_definitions(min_confidence=0.7)` 全量召回，未考虑跨数据库时指标定义可能不通用。

**目标**：

- 一次启动可服务全部数据库
- 请求参数显式传 `user_id` / `session_id` / `db_id`，决定使用哪套数据库资源、复用哪段会话历史与用户记忆
- 控制内存占用（单 worker 部署，BGE 等大模型只加载一次）

**核心决策**

采用 **「全局单例 + DbContext LRU 池」** 双层架构：

```
┌─────────────────────────────────────────────────────┐
│         全局单例层（启动加载，进程级永生）              │
├─────────────────────────────────────────────────────┤
│  BGE-M3 (SchemaVectorizer)   ~2GB                   │
│  VectorStore (chroma 共用)    ~50MB                  │
│  LLMClient                                          │
│  SQLGenerator                                       │
│  SelfConsistencyDecision                            │
│  AnswerabilityChecker                               │
│  ResultVerifier                                     │
│  HistoryCache                                       │
│  MemoryUpdater                                      │
│  SessionManager (文件存储)                            │
└─────────────────────────────────────────────────────┘
                       ▲
                       │ 被引用
                       │
┌─────────────────────────────────────────────────────┐
│       DbContextPool (max=2，按 db_id 懒加载)         │
├─────────────────────────────────────────────────────┤
│  DbContext("california_schools")                    │
│      ├─ DatabaseConnector                           │
│      ├─ LSHIndexer                                  │
│      ├─ InformationRetrieval（共享 BGE / chroma）    │
│      ├─ SchemaSelector                              │
│      ├─ SQLExecutor                                 │
│      ├─ SQLFixLoop                                  │
│      └─ CompiledGraph（每个 db 一份主图）            │
│                                                     │
│  DbContext("financial")                             │
│      └─ ... 同上                                    │
└─────────────────────────────────────────────────────┘
```

**关键约束**

| 资源                  | 生命周期            | 数量          | 备注                            |
|----------------------|---------------------|---------------|---------------------------------|
| BGE-M3 / VectorStore | 进程级永生           | 1            | 与 db_id 无关，所有 db 共用       |
| LLM / Generator / Decider / Answerability / HistoryCache / MemoryUpdater | 进程级永生 | 各 1 | 无状态或与 db 无关 |
| SessionManager       | 进程级永生           | 1            | 内部 LRU + 文件持久化             |
| UserMemory           | LRU 缓存（已有）     | 0 ~ 100       | 现有 `_user_memory_cache` 不动   |
| DbContext            | LRU 淘汰             | 0 ~ 2         | 按 db_id 懒加载                  |

**DbContextPool 行为约定**

- `max_size = 2`（环境变量 `DB_POOL_MAX_SIZE` 可覆盖）
- 首次访问某 db_id → 现场构造（首请求慢 5-10 秒，加载 LSH + 建主图）
- 命中已缓存 db → 立即返回，并在 OrderedDict 中 move_to_end（LRU 提升）
- 池满淘汰策略：**淘汰最久未用且 refcount=0 的 ctx**；若所有 ctx 都在使用中，**跳过淘汰并允许池短暂超 max**（不阻塞请求）
- 淘汰时调用 `ctx.close()` 释放 sqlite 连接

**请求路径变化**

```
   旧：POST /query {user_id, session_id, query}
        └─ get_graph()  ← 进程唯一的 graph，绑死一个 db

   新：POST /query {user_id, session_id, db_id, query}
        ├─ get_db_pool().acquire(db_id) → DbContext
        │     └─ 用 db_ctx.graph 跑主图
        │     └─ finally: pool.release(db_id)
        ├─ get_user_memory(user_id) ← 已有 LRU
        └─ session_manager.get_or_create_session(session_id, user_id)
```

**启动模型**

- **不预加载所有 db**：启动只装全局单例（BGE + LLM + 各 Agent + SessionManager + 空 DbContextPool）
- **可选 warm-up**：`run_api.py --db_id <id>` 在 uvicorn 启动前预加载指定 db，避免首请求慢
- **lifespan 集中初始化**：从 `run_api.py:bootstrap` 改为 `src/api/app.py:lifespan` startup 钩子调用 `init_globals()`；shutdown 钩子调 `pool.close_all()`

**并发与生命周期**

- 仅支持**单 worker** (`uvicorn.run(app, workers=1)`)：不引入 Redis 等共享存储
- FastAPI 异步框架下多协程并发：`DbContextPool` 用 `threading.RLock()` 保护 dict 操作
- DbContext 引用计数防淘汰：query handler 用 `try/finally` 包 `acquire/release`
- SessionManager 文件并发：单 worker 下进程内 LRU + 文件 IO 不会出现 lost update

**Memory 召回策略**

- `user_memory` 全量加载（一个用户的记忆容量小），但调用 `get_metric_definitions(min_confidence)` 时按相关性过滤
- `session_memory` 按 `session_id` 隔离，每次请求注入最近 N 轮历史
- 未引入「按 db_id 分桶 user_memory」（用户记忆容量小，跨 db 的指标定义可由 LLM 在 prompt 中自行判断是否适用）

**API 字段变化**

```diff
  POST /api/v1/query
  {
    "query":      str,
    "session_id": str,
    "user_id":    str,
+   "db_id":      str       ← 新增必填
  }
```

新增端点：

```
  GET  /api/v1/databases                 → 列出 data/ 下所有可用 db_id
  GET  /api/v1/databases/{db_id}/tables  → 列出指定 db 的表清单
  POST /api/v1/sessions                  → 显式创建新会话（body: {user_id, db_id?}）
```

**为什么不引入 Redis / Backend 抽象**

- 单 worker 部署下，进程内 LRU 与文件存储天然一致，无需共享存储
- 抽象层（Backend Protocol）属 YAGNI：未来确需多 worker 时再重构 SessionManager / UserMemory 的存储后端
- 大幅减少改动面（仅 ~9 个文件），降低出错风险

**风险与缓解**

| 风险                                       | 缓解                                     |
|------------------------------------------|-----------------------------------------|
| 首次访问冷 db 时请求超时（5-10 秒）           | 提供 `--db_id` warm-up；前端展示加载提示    |
| 内存预算超出（BGE 2GB + 2 × ~50MB LSH）     | DB_POOL_MAX_SIZE 默认 2；可通过配置调整   |
| LRU 淘汰时 sqlite 连接被在用请求持有         | 引用计数：refcount > 0 则跳过该 ctx 的淘汰 |
| 多协程并发构造同一 db                       | RLock 包裹整个 acquire；幂等 double-check |
| 未来切换多 worker 需要重构                  | 接受 YAGNI：当前不投入抽象成本             |

**取代关系**

本决策**取代**决策 31 中以下内容：
- 「启动时一次性加载所有组件」→ 改为「启动加载全局单例 + 按需懒加载 DbContext」
- 「会话管理器：内存 LRU 缓存 + 持久化 JSON」→ 保留（不变）
- API 服务架构改为多数据库分池模型

决策 31 的 API 端点设计（SSE 事件序列、health 接口等）仍然有效。

---

### 决策 50：API 真流式响应 + LLM 思考链推送

**背景与问题**

当前 `src/api/routes/query.py` 的 `event_stream()` 实现存在严重缺陷：

```python
# 现状（query.py:88）
stream_results = await loop.run_in_executor(None, _run_stream, graph, initial_state)
for update in stream_results:        # ← 先把整个 graph 跑完再 yield
    yield _format_sse(...)
```

`_run_stream` 在 executor 中把 `graph.stream()` 跑完、把所有 update 收进 list，**再**回到 async 上下文 yield SSE 事件。等价于"先做完所有工作，再一次性吐出全部进度"。

实测一条 query 在 California Schools 库上：
- 总耗时约 5 分钟
- 200 OK 在 3 秒时写出
- body 第一个字节在 5 分钟后才生成
- 客户端 httpx 默认 5 秒读超时，直接 `httpx.ReadTimeout`

且节点内部 LLM 调用本身耗时数十秒（关键词提取 60s、列相关性评估 80s、可回答性 51s 等），用户在节点内部毫无进度感知。

**目标**

1. **真流式**：每个 LangGraph 节点完成时立即 yield SSE，客户端实时可见进度。
2. **思考链推送**：把 qwen3 等模型的 `reasoning_content`（自然语言思考过程）作为独立事件流推送，让用户能"读懂模型在想什么"。
3. **不推 JSON 业务输出 token**：业务调用全部用 `chat_json`（JSON 模式），token 流出来是 JSON 片段（`{`、`"answerable"`、`:` ...）不可读。不向客户端推送这类 chunk，等 LLM 完整返回 + 解析后再以结构化事件推送（如 `answerability`、`keywords`、`sql_candidates` 等）。
4. **心跳防断流**：每 15 秒发心跳防止客户端/反向代理超时。

**核心改动**

```
┌──────────────────────────────────────────────────────────────┐
│                  改造前后对比                                  │
└──────────────────────────────────────────────────────────────┘

   改造前（伪流式）                  改造后（真流式 + 思考链）
   ━━━━━━━━━━━━━━━━━━                ━━━━━━━━━━━━━━━━━━━━━━━━

   T=0   POST /query                  T=0    POST /query
         (no body)                    T=0.1  stage(history_cache, started)
         ...                          T=2    stage(history_cache, done)
         5 分钟等待                   T=2.1  stage(ir, started)
         ...                          T=2.5  llm_thinking("我需要分析这个问题...")
   T=5m  yield stage(ir,done)         T=5    llm_thinking("应该提取出'学校'和'各科成绩'...")
         yield stage(ss,done)         T=60   keywords({groups:[...]})
         yield stage(cg,done)         T=60.1 stage(ir, done)
         yield result                  T=60.1 stage(ss, started)
         yield done                    T=80   llm_thinking("评估列相关性...")
                                        ...
                                        T=5m   final_decision({...})
                                        T=5m   result(sql=..., data=...)
                                        T=5m   done
```

**架构：contextvars + asyncio.Queue 三层桥接**

```
┌─────────────────────────────────────────────────────────────────┐
│   ┌──────────────────┐                                          │
│   │ FastAPI handler  │  asyncio loop                            │
│   │  event_stream()  │◄────────────────── async for evt in Q   │
│   └────────┬─────────┘                                          │
│            │ run_in_executor                                    │
│   ┌────────▼─────────┐                                          │
│   │ Thread (sync)    │  graph.stream(state)                     │
│   │  graph 执行       │  每个 node 完成 → Q.put_nowait(...)     │
│   └────────┬─────────┘                                          │
│            │ contextvar 注入 stream_emitter                      │
│   ┌────────▼─────────┐                                          │
│   │ LLMClient.chat   │  stream=True 接收 token chunk           │
│   │   _stream(...)   │  仅当 reasoning_content 非空时           │
│   │                  │  emitter.emit("llm_thinking", reasoning) │
│   │                  │  正文 content 累积为完整字符串后返回       │
│   └──────────────────┘  → Q.put_nowait(...)                     │
│                                                                 │
│   ┌──────────────────────────────────────┐                      │
│   │ asyncio.Queue (线程安全 put_nowait)   │                      │
│   └──────────────────────────────────────┘                      │
└─────────────────────────────────────────────────────────────────┘
```

**关键设计点**

1. **StreamEmitter（事件发射器）**

```python
# src/api/streaming.py
class StreamEmitter:
    """线程安全的 SSE 事件发射器"""
    def __init__(self, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop):
        self.queue = queue
        self.loop = loop

    def emit(self, event_type: str, data: dict) -> None:
        # 从同步线程往 asyncio.Queue 推事件
        self.loop.call_soon_threadsafe(
            self.queue.put_nowait, {"type": event_type, "data": data}
        )

# contextvar 传递 emitter，无需修改函数签名
current_emitter: ContextVar[Optional[StreamEmitter]] = ContextVar(
    "current_emitter", default=None
)
```

2. **LLMClient 增加思考链流式方法**

```python
# utils/llm_client.py 新增
def chat_stream(self, messages, on_thinking=None, response_format=None, **kw) -> str:
    """
    流式调用，仅把 reasoning_content（思考链）实时回调；
    正文 content 累积为完整字符串后返回（用于后续 json.loads）

    设计决策：不推送正文 token（业务调用全是 JSON 模式，token 不可读）。
    思考链是自然语言，对用户友好，单独推送。
    """
    stream = self.client.chat.completions.create(
        model=self.model, messages=messages, stream=True,
        response_format=response_format,
        extra_body={"enable_thinking": True}  # qwen3 思考链
    )
    full_text = ""
    for chunk in stream:
        delta = chunk.choices[0].delta
        # 思考链：自然语言 → 实时推送
        if hasattr(delta, "reasoning_content") and delta.reasoning_content:
            on_thinking and on_thinking(delta.reasoning_content)
        # 正文：JSON 片段 → 仅累积，不推送
        if delta.content:
            full_text += delta.content
    return full_text

def chat_json(self, messages, ...):
    """改造：内部检查 current_emitter，有则走 chat_stream 推送思考链"""
    emitter = current_emitter.get()
    if emitter is None:
        return self._chat_json_blocking(messages, ...)   # 旧路径
    node_name = current_node.get() or "unknown"
    text = self.chat_stream(
        messages,
        on_thinking=lambda c: emitter.emit("llm_thinking", {"node": node_name, "text": c}),
        response_format={"type": "json_object"},
    )
    return self._parse_json(text)  # 解析为 dict + 正则兜底
```

3. **节点感知（当前所在节点名）**

也用 contextvar：

```python
current_node: ContextVar[Optional[str]] = ContextVar("current_node", default=None)

# 在 main_graph.py 的节点工厂里包一层：
def make_ir_node(retriever):
    def node(state):
        current_node.set("ir")
        emitter = current_emitter.get()
        emitter and emitter.emit("stage", {"node": "ir", "status": "started"})
        try:
            result = retriever.build_graph().invoke(...)
            emitter and emitter.emit("stage", {"node": "ir", "status": "done",
                                                "keywords": result.get("keywords")})
            return {...}
        finally:
            current_node.set(None)
    return node
```

4. **event_stream 重写**

```python
async def event_stream():
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_event_loop()
    emitter = StreamEmitter(queue, loop)
    sentinel = object()

    def run_graph():
        token = current_emitter.set(emitter)
        try:
            for update in graph.stream(initial_state):
                # graph.stream 本身的 update 也推（节点级 raw 更新）
                emitter.emit("graph_update", _serialize(update))
        finally:
            current_emitter.reset(token)
            queue.put_nowait(sentinel)

    asyncio.create_task(asyncio.to_thread(run_graph))

    last_heartbeat = time.time()
    while True:
        try:
            evt = await asyncio.wait_for(queue.get(), timeout=15.0)
        except asyncio.TimeoutError:
            yield ": heartbeat\n\n"
            continue
        if evt is sentinel:
            yield _format_sse("done", {"has_result": bool(...)})
            break
        yield _format_sse(evt["type"], evt["data"])
```

**SSE 事件类型表（决策 50 定稿）**

| 事件类型          | 触发时机                          | data 字段                              |
|------------------|----------------------------------|---------------------------------------|
| `stage`          | 节点开始/结束                     | `{node, status: started/done, ...}` |
| `cache_check`    | history_cache 完成                | `{hit, source, confidence, cached_sql}` |
| `llm_thinking`   | qwen3 reasoning_content chunk     | `{node, text}` ← 自然语言思考片段       |
| `keywords`       | IR 关键词提取完成                  | `{groups: [...]}`                    |
| `schema_recall`  | IR schema 召回完成                | `{groups: [{name, top_columns}]}`    |
| `answerability`  | 可回答性检查完成                  | `{answerable, confidence, reason}`   |
| `sql_candidates` | CG 候选 SQL 生成完成              | `{candidates: [{id, sql}]}`          |
| `execution`      | 每个候选 SQL 执行完成              | `{candidate_id, success, rows}`      |
| `final_decision` | Decision 完成                     | `{selected_id, reason}`              |
| `result`         | 最终结果                         | `{sql, result}`                      |
| `error`          | 任意环节错误                     | `{error, node?}`                     |
| `done`           | 整条 query 完成                   | `{has_result}`                       |
| `: heartbeat`    | 15 秒内无事件                     | （SSE 注释行）                        |

**注**：决策 50 不推送 `llm_chunk`（业务 LLM 调用全是 JSON 模式，token 是 `{`、`"answerable"`、`:` 之类的片段，对用户没价值）。正文等 LLM 完整返回 + 解析后以结构化事件（`answerability` / `keywords` 等）一次性推出。思考链是自然语言，独立推送为 `llm_thinking`。

**客户端配合**

httpx 配置必须解除 read timeout：

```python
with httpx.stream(..., timeout=httpx.Timeout(connect=10, read=None, write=10, pool=10)):
    ...
```

或保留 timeout 但依赖心跳——每个心跳重置客户端读计时器。

**约束与限制**

- **JSON 模式与流式的兼容**：OpenAI SDK 在 `stream=True` 下不支持 `response_format={"type": "json_object"}`。采用变通方案：流式拿到完整文本后再 `json.loads`，prompt 中加强 JSON 输出约束。
- **思考链开关**：`enable_thinking` 是 qwen3 专属 `extra_body`。其他模型时该开关无效，`reasoning_content` 字段为 None，自动降级为不发 `llm_thinking` 事件。
- **回退路径保留**：`current_emitter.get() is None` 时 LLMClient 走旧的阻塞 `chat`/`chat_json` 路径，保证测试、CLI、离线脚本不受影响。

**风险与缓解**

| 风险                                       | 缓解                                  |
|------------------------------------------|-------------------------------------|
| 流式 + JSON 模式可能产生不可解析 JSON         | 末尾尝试 `re.search(r'\{[\s\S]*\}', text)` 兜底；prompt 强约束 JSON 输出 |
| 思考链 token 量大（一次 query 可能数 KB）     | 客户端按 node 分组累加显示；网络开销可控 |
| contextvar 在 thread executor 中失效        | 用 `contextvars.copy_context().run()` 显式传递 |
| 反向代理（nginx）缓冲 SSE 导致延迟           | response headers 已带 `X-Accel-Buffering: no`；心跳保活 |
| qwen3 思考链占用大量推理时间                  | 提供 `enable_thinking` 配置项；默认开，可关闭 |
| 非 qwen3 模型无 reasoning_content           | `chat_stream` 检测字段存在性，无则不推送 llm_thinking |
| 旧调用方（测试）依赖 `chat()/chat_json()`     | 保留旧方法签名，emitter 未设置时走旧路径 |

**取代关系**

- **取代决策 31 的"伪流式 SSE 实现"**：从"先攒后吐"改为"边跑边吐 + token 级"。
- 不取代 §22 决策 49 的 DbContextPool 设计，§23 在其基础上增强。

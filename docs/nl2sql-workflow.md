# NL2SQL 完整工作流程文档

> 版本: 0.2.0 · 更新日期: 2026-06-25
>
> 本文件是对整个 NL2SQL 系统的完整运行流程说明，涵盖从用户输入自然语言到最终输出 SQL 结果的全链路。面向读者：希望深度了解系统工作原理的开发者。每个章节都包含设计意图、关键逻辑说明、以及实践中需要注意的边界情况。

---

## 目录

1. [系统架构总览](#1-系统架构总览)
2. [主图流程（Node by Node）](#2-主图流程)
3. [IR 子图 — 信息召回](#3-ir-子图)
4. [Clarification 节点 — 澄清反问](#4-clarification-节点)
5. [SS 子图 — Schema 选择](#5-ss-子图)
6. [AnswerabilityCheck 节点 — 可回答性检查](#6-answerabilitycheck-节点)
7. [CG 子图 — SQL 生成](#7-cg-子图)
8. [Execution 节点 — 候选执行](#8-execution-节点)
9. [Decision 子图 — 自洽性决策](#9-decision-子图)
10. [Memory Update 节点 — 记忆自动学习](#10-memory-update-节点)
11. [记忆系统详解](#11-记忆系统)
12. [API 层与依赖注入](#12-api-层)
13. [SSE 流式事件](#13-sse-流式事件)
14. [配置参考](#14-配置参考)

---

## 1. 系统架构总览

### 1.1 整体设计思想

NL2SQL 是一个用 LangGraph 编排的**多 Agent 流水线系统**。整个流程像一条智能生产线：用户输入一句自然语言（如"查询上个月各门店的销售额排名"），系统依次经过**历史缓存 → 信息召回 → Schema 选择 → 可回答性检查 → SQL 生成 → 执行 → 自洽性决策 → 记忆更新**八个阶段，最终输出可信的 SQL 和查询结果。

设计上的几个关键决策：

- **流水线而非端到端**：把问题拆成多个独立阶段，每个阶段专注一件事。好处是每个阶段的输入输出明确，可以单独测试、替换、优化。坏处是阶段间信息有损耗，所以我们在 State 中保留了完整的中间产物。
- **多候选 + 自洽性**：CG 阶段不生成一条 SQL，而是生成 3-5 个候选。Decision 阶段通过两轮评分（看结果、看 SQL）+ 修复循环来保证最终选出的 SQL 可信。这是本系统最核心的质量保障机制。
- **先召回后选择**：IR 只做宽召回（LSH + 向量），SS 做精选择（LLM 评估），避免一开始就把候选集收得太窄导致遗漏。
- **可回答性检查 + 最终验证两道闸门**：在 Schema 就绪后判断问题是否可回答（避免"不存在的数据"场景），在最终决策后验证结果是否可信（避免答非所问）。

### 1.2 核心流程图

```
用户查询 ──→ [API Gateway]
                │
                ▼
         ┌──────────────┐
         │ HistoryCache  │─── 命中 → 直接执行缓存 SQL（绕过后半段链路）
         └──────┬───────┘
                │ (cache miss)
                ▼
         ┌──────────────┐      ┌──────────────────┐
         │  IR 子图      │─────▶  Keyword 提取     │
         │ (Information   │      │  Schema 向量召回  │
         │  Retrieval)    │      │  LSH 值召回       │
         └──────┬───────┘      └──────────────────┘
                │
                ▼
         ┌──────────────┐
         │ Clarification │─── 设计为对话澄清（当前 phase 1 为 pass-through）
         └──────┬───────┘
                │
                ▼
         ┌──────────────┐
         │  SS 子图      │─────▶  M-Schema 转换
         │ (Schema       │        LLM 列相关性评估
         │  Selection)   │        过滤不相关列
         └──────┬───────┘
                │
                ▼
         ┌─────────────────┐
         │ Answerability    │─── 不可回答 → END（拒答 + 原因）
         │ Check            │
         └──────┬──────────┘
                │ (可回答)
                ▼
         ┌──────────────┐
         │  CG 子图      │─────▶  实体提取
         │ (SQL           │        Query 掩码
         │  Generation)   │        Few-shot 示例选择
         │                │        LLM 生成 N 个候选
         │                │        sqlglot 安全验证
         └──────┬───────┘
                │
                ▼
         ┌──────────────┐
         │ Execution     │─────▶  缓存命中 → 直接执行缓存 SQL
         │ (ExecuteAll)  │        否则 → 一次性执行 N 个候选
         └──────┬───────┘       不触发 LLM 修复（修复推迟到 Decision）
                │
                ▼
         ┌──────────────────────────────────┐
         │  Decision 子图                   │
         │   filter_success                 │
         │       │                          │
         │       ├── 有成功候选 → R1 数据评分 │
         │       │       ├── 唯一=5 → 直选   │
         │       │       ├── 并列=5 → R2 SQL│
         │       │       └── <5    → SmartFix│
         │       │                          │
         │       └── 全失败 → 按错误分级修复  │
         │              ├── 成功 → 返回      │
         │              └── 全不可修 → 拒答  │
         └──────────────┬───────────────────┘
                        │
                        ▼
         ┌──────────────┐
         │ Memory Update │─────▶  常用表学习
         │ (自动学习)     │        指标定义学习
         └──────┬───────┘        查询偏好学习
                │                会话上下文更新
                ▼                澄清历史写入
            [SSE Response]       成功 query 写入 SessionMemory v2

  ──→ 返回 SQL + 结果到客户端
```

### 1.3 模块依赖关系

系统启动时由 `deps.init_globals()` 统一构造所有全局单例和每个数据库独立的 DbContext。模块依赖关系如下：

```
启动入口: run_api.py → app.py (lifespan) → deps.init_globals()
                                                        │
                    ┌───────────────────────────────────┼───────────────────────────┐
                    ▼                                   ▼                           ▼
            BGE-M3 向量化器                    LLM Client (Qwen)          Chroma VectorStore
            (SchemaVectorizer)                     │                    (nl2sql_columns)
                    │                              │                           │
                    ▼                              ▼                           ▼
            ┌──────────────────────────────────────────────────────────────────────┐
            │                         SQLGenerator                                │
            │               SelfConsistencyDecision                                │
            │               AnswerabilityChecker                                   │
            │               HistoryCache                                           │
            │               MemoryUpdater                                          │
            └──────────────────────────────────────────────────────────────────────┘
                                         │
                                         ▼
            ┌──────────────────────────────────────────────────────────────────────┐
            │  SessionMemory v2 召回组件:                                          │
            │    ChromaSessionQueryIndex + JsonConversationStore                   │
            │    + LocalBM25Retriever + RRFRanker + HybridSessionRetriever         │
            └──────────────────────────────────────────────────────────────────────┘
                                         │
                    ┌────────────────────┴────────────────────┐
                    ▼                                         ▼
            SessionManager                              DbContextPool
        (data/sessions/)                              (LRU, max_size=2)
                                                              │
                                                    ┌─────────┴─────────┐
                                                    ▼                   ▼
                                            DbContext(db1)      DbContext(db2)
```

在运行时，`main_graph.py` 用这些组件构造主图。每个子图的内部状态是独立的（`IRGraphState`、`SSGraphState` 等），主图 `NL2SQLState` 只负责跨子图的字段传递。

### 1.4 文件结构与职责

| 文件 | 职责 |
|------|------|
| **主图编排** | |
| `src/graph/main_graph.py` | 主图：节点包装（SSE + 日志）、条件边、子图编排 |
| `src/graph/state.py` | NL2SQLState（TypedDict）：全链路共享状态 + `create_initial_state()` |
| **IR 子图** | |
| `src/retrieval/ir_graph.py` | IR 子图状态 + 4 节点编排 + build_graph() |
| `src/retrieval/information_retrieval.py` | IR 核心：关键词提取、LSH/向量检索、RetrievedContext 装配 |
| **SS 子图** | |
| `src/schema_selection/ss_graph.py` | SS 子图状态 + 3 节点编排 + build_graph() |
| `src/schema_selection/schema_selector.py` | SS 核心：Schema/M-Schema 转换、LLM 列相关性评估、过滤 |
| **CG 子图** | |
| `src/sql_generation/cg_graph.py` | CG 子图状态 + 4 节点编排 |
| `src/sql_generation/sql_generator.py` | CG 核心：实体提取、value 映射、template 生成、LLM 生成、sqlglot 安全验证 |
| **Execution** | |
| `src/execution/execution_graph.py` | Execution Agent 子图（含修复循环，当前主图已移到 Decision 做修复）|
| `src/execution/executor.py` | SQLFixLoop + Executor：多数据库引擎、错误类型分级(ERROR_SEVERITY)、SmartFix |
| **Decision 子图** | |
| `src/decision/decision_graph.py` | Decision 子图：8 节点 + 条件路由 + 8 条路径 A-H + SmartFix 集成 |
| `src/decision/self_consistency.py` | R1/R2 评分、`_pick_from_scores` 选候选、`pick_lightest_failures` 错误分级、数据预览截断 |
| `src/decision/prompts.py` | R1 数据评分 / R2 SQL 评分 / SmartFix 修复的 LLM Prompt 模板 |
| **Verification** | |
| `src/verification/answerability.py` | 可回答性检查（SS→CG 之间）|
| `src/verification/result_verifier.py` | 结果可信度验证（Decision 末尾）|
| **记忆系统** | |
| `src/memory/history_cache.py` | HistoryCache：LLM 判断当前查询是否能复用历史 SQL |
| `src/memory/user_memory.py` | UserMemory：6 个预定义 Topic 的长期记忆，schema 治理 |
| `src/memory/memory_updater.py` | MemoryUpdater：SQL→表/指标/偏好 的自动学习、SessionMemory v2 写入 |
| `src/memory/session_recall.py` | SessionMemory v2：ChromaSessionQueryIndex / JsonConversationStore / LocalBM25Retriever / RRFRanker / HybridSessionRetriever |
| `src/memory/session_manager.py` | SessionManager：多轮对话管理、文件存储 + LRU 缓存 |
| `src/memory/storage.py` | 存储基础设施（原子读写）|
| **API 层** | |
| `src/api/deps.py` | 依赖注入：init_globals() 启动初始化、get_globals/get_db_pool 等依赖函数 |
| `src/api/db_pool.py` | DbContextPool + Globals：按 db_id 懒加载 DbContext，LRU 淘汰 |
| `src/api/app.py` | FastAPI 应用：lifespan、路由注册、CORS、健康检查 |
| `src/api/streaming.py` | SSE 流式推送基础设施 + heartbeat |
| **预处理器** | |
| `src/preprocessing/build_lsh_index.py` | LSH 索引构建（离线）|
| `src/preprocessing/build_schema_index.py` | Schema 向量索引构建（离线）|
| `src/preprocessing/schema_vectorizer.py` | BGE-M3 向量化器封装 |


---

## 2. 主图流程

### 2.1 主图是怎么工作的

主图 `main_graph.py` 本质上是一个**适配器层**。它的每个节点并不直接包含业务逻辑——业务逻辑在各 Agent 类（`InformationRetrieval`、`SchemaSelector`、`SQLGenerator` 等）中。主图节点做的是：

1. 把 `NL2SQLState` 的字段映射到子图所需的内部 State
2. 调用子图的 `.build_graph()` 获取已编译的 LangGraph
3. 把子图输出的字段映射回 `NL2SQLState`

这种"适配器"模式的好处是：子图可以独立测试、独立替换，主图只负责编排和条件路由。

另一个重要设计是**节点装饰器 `_wrap_node()`**。每个节点函数被包了一层，自动做三件事：
- 进入/退出时发 `stage` SSE 事件（前端可以看到当前执行到哪一步）
- 设置 `current_node` ContextVar（供流式组件追踪当前节点）
- 记录 `[qid=xxx] [stage] node=xxx status=started/done` 的结构化日志

### 2.2 节点列表与执行顺序

```
START → history_cache → [条件] → ir → clarification → [条件] → ss
       → [条件] → answerability_check → [条件] → cg → [条件] → execution
       → decision → memory_update → END
```

每个阶段的意义：

| 节点 | 做什么 | 为什么在这 |
|------|--------|----------|
| `history_cache` | 检查当前 query 能否复用历史 SQL | **最先做**，命中就直接跳到 execution，绕过后半段所有 LLM 调用，大幅节省耗时和 token |
| `ir` | 检索相关 schema（表、列、值）| **必须做**，后续所有环节（SS/CG/Execution）都需要 schema 信息 |
| `clarification` | 对模糊/歧义查询做反问澄清 | Phase 1 跳过，Phase 2 会放在 IR 后、SS 前——因为只有看完 IR 召回结果才能判断是否清晰 |
| `ss` | 从 IR 召回结果中精确选择需要的表和列 | IR 是宽召回可能有噪声，SS 做精筛 |
| `answerability_check` | 判断当前 schema 下问题能否回答 | SS 之后才有完整 schema，所以放在这 |
| `cg` | 生成 N 个候选 SQL | schema 选好了才能生成 SQL |
| `execution` | 一次性执行所有候选 SQL | CG 产出候选后立刻执行，为 Decision 提供结果数据 |
| `decision` | 评分 + 修复 + 验证，选出最终结果 | 这是最终决策环节，放在最后 |
| `memory_update` | 从本轮结果自动学习记忆 | 决策完成后才能学习 |

### 2.3 条件边与兜底机制

条件边是 LangGraph 的关键能力——让流程可以根据中间状态动态选择下一步。本系统有 5 个条件边：

| 位置 | 条件 | 走哪边 | 设计意图 |
|------|------|--------|---------|
| history_cache 后 | `cache_hit=True` | 直接→execution | 缓存命中意味着 SQL 已知，无需重新召回和生成 |
| history_cache 后 | `cache_hit=False` | →ir | 正常走全链路 |
| clarification 后 | `clarification_done=True` | →ss | 澄清完成或不需要澄清 |
| clarification 后 | `clarification_done=False` | 循环回自身 | Phase 2 多轮反问 |
| ss 后 | `selected_schema` 为空 | →END | 没有可用的 schema 就无法生成 SQL，直接结束 |
| ss 后 | `selected_schema` 非空 | →answerability_check | 正常继续 |
| answerability_check 后 | `answerable="false"` | →END（拒答）| 明确不可回答，提前结束 |
| answerability_check 后 | `answerable` 非 false | →cg | true/uncertain 都放行 |
| cg 后 | `sql_candidates` 为空 | →END | 没生成 SQL 就结束 |
| cg 后 | `sql_candidates` 非空 | →execution | 正常继续 |

**兜底原则**：当遇到异常情况时，系统倾向于**安全结束**而非强行继续。例如 SS 产出空 schema、CG 没有候选、Decision 全部失败——都会干净地结束并返回错误信息，不会卡死或产生垃圾结果。

### 2.4 State 字段的完整流转

`NL2SQLState` 是 TypedDict，LangGraph 在每个节点返回 dict 后会做浅合并。这意味着 List/Dict 类型字段如果要追加，节点内部必须先复制再追加再返回，避免就地修改导致 LangSmith trace 不准。

关键字段的写入和读取关系：

| 字段 | 谁写入 | 谁读取 | 字段意义 |
|------|--------|--------|---------|
| `user_query` | 入口（START）| 所有节点 | 原始用户查询，全程只读 |
| `user_id` / `database_filter` | 入口 | history_cache, cg, memory_update | 身份和数据库路由 |
| `query_id` | 入口（API 层生成 uuid4）| 日志、SSE 事件 | 请求级追踪 ID |
| `cache_hit` / `cached_sql` / `cache_source` / `cache_confidence` | history_cache | execution | 缓存命中的全量信息 |
| `historical_sql_refs` | history_cache | cg | 不可复用历史的 SQL 弱参考 |
| `keywords` | ir | SSE 事件 | 关键词提取结果，用于前端展示 |
| `retrieved_context` | ir | ss | 召回综合结果（表/列/值/JOIN 路径）|
| `selected_schema` | ss | answerability_check, cg | 裁剪后的 schema（MSchemaTable 列表）|
| `clarification_history` | clarification | memory_update | 反问交互历史 |
| `sql_candidates` | cg | execution, decision | N 个候选 SQL（含状态和执行结果）|
| `schema_text` | execution | decision, smart_fix | schema 的 LLM 文本格式（避免每次都重新格式化）|
| `final_decision` / `final_sql` / `final_result` | decision | memory_update, SSE | 最终决策输出 |
| `candidate_scores_r1/r2` | decision | SSE 事件 | 评分明细 |
| `fix_failed` / `fix_rounds_used` / `decision_path` | decision | SSE 事件 | SmartFix 结果 |
| `rejection_reason` | answerability_check / decision | SSE 事件 | 拒答原因 |
| `error` | 任意节点 | SSE 事件 | 节点级错误信息 |
| `_user_memory` / `_session_memory` | API 层注入 | history_cache, cg, memory_update | 记忆实例指针 |


---

## 3. IR 子图

### 3.1 为什么需要 IR

IR 是"信息召回"（Information Retrieval）的缩写。用户的自然语言要转成 SQL，第一步是**理解用户想查什么**，然后**在数据库的 schema（表、列、关系）中找到对应的东西**。

IR 采用"关键词提取 + LSH 值检索 + 向量 Schema 检索 + 装配增强"的串行流程。注意这里没有用并行——因为现阶段用户查询的召回响应时间主要消耗在 LLM 调用（关键词提取）上，内部步骤的串行开销几乎可以忽略。如果将来需要，可以用 LangGraph 的 Send API 做真正的并行 fork。

### 3.2 子图拓扑

```
extract_keywords → retrieve_values → retrieve_schema → assemble
```

### 3.3 extract_keywords — 关键词提取

**做什么**

调用 LLM + few-shot prompt，从用户自然语言中提取结构化的关键词。返回的不是简单字符串列表，而是 `List[KeywordGroup]`。每个 `KeywordGroup` 包含一个核心词（phrase）和它的同义词列表（terms）。

**为什么这么设计**

用户表达同一个概念可能用不同的词。比如"销售额"可能是"sales"、"gmv"、"营收"。KeywordGroup 的 terms 列表就是用来兜住这些同义表达的。后续的向量召回会用所有 terms 分别检索，避免漏掉。

**输入**：`user_query` + 可选的 `conversation_history`（follow-up 理解用）

**输出**：`keywords`（List[KeywordGroup]）+ 扁平化的 `flat_terms`

**边缘情况处理**：
- 如果用户查询很短（如"查一下"），LLM 可能返回空关键词 → 后续向量召回会回退到全文检索
- 如果有 conversation_history 但跟上轮无关 → LLM 会忽略无关历史

### 3.4 retrieve_values — LSH 值检索

**做什么**

用 LSH（Locality-Sensitive Hashing，基于 datasketch MinHash）在数据库各个字段的唯一值索引中做精确匹配。比如用户说"查张三的订单"，LSH 会把"张三"匹配到 `customer.name` 字段中的具体值。

**为什么用 LSH 而不是直接用 SQL LIKE**

- LSH 是**离线预构建索引**的，查询时不用扫描全表
- LSH 支持模糊匹配（相似的字符串也能命中），比精确 LIKE 更鲁棒
- 但它只能查**已建索引的值**，所以需要离线预处理（`build_lsh_index.py`）

**输入**：`flat_terms`（扁平化后的所有同义词字符串）

**输出**：`values`（List[RetrievedItem]，每个 item 包含匹配到的表、列、值和相似度分数）

### 3.5 retrieve_schema — 向量 Schema 检索

**做什么**

用 BGE-M3 把列/表的描述文本向量化存在 ChromaDB 中，然后用用户查询的关键词做语义相似度搜索。这是本系统最重要的召回通道——它能找到用户在语义上相关的表和列，即使字面上不完全匹配。

**为什么要分组检索**

IR 的做法是按 KeywordGroup **分组独立召回**，然后跨组去重。这样做的原因是：不同关键词可能对应不同领域的列（比如"价格"对应的列和"销量"对应的列可能完全不同）。分组检索保证每个关键词组都能召回到各自领域的结果，而不会被其他关键词"淹没"。

**跨组去重逻辑**：同一个列可能被多个关键词组召回到，保留得分最高的那次。

**从列反推表**：召回结果是列级别的，但下游 SS 需要表级别的信息。所以 IR 会从召回的列反推这些列所属的表，同时补充表级别信息。

**输入**：`keywords`（KeywordGroup 列表）+ `database_filter`（限定数据库）

**输出**：`schema_tables`, `schema_columns`, `keyword_columns_map`

**性能说明**：BGE-M3 在 CPU 上单次 embedding 约 100-200ms。对于常见查询（3-5 个关键词组），总耗时约 500ms-1s。

### 3.6 assemble — 装配与增强

**做什么**

把前三个节点的产出整合为一个完整的 `RetrievedContext` 对象，然后做两件事：

1. **反推表覆盖**：LSH 值检索到的值，如果它的表没有被向量检索召回，补进来。保证"值存在但 schema 没被召回到"的情况不丢数据。
2. **注入 JOIN 路径**（决策 26）：根据数据库的外键关系，自动补充跨表关联路径。比如用户查"订单金额和客户姓名"，即使只召回了 `orders` 和 `customers` 两张表，系统会自动在 context 中标注它们的 JOIN 关系（`orders.customer_id = customers.id`），让 SS 和 CG 可以直接用。

**输入**：keywords, values, schema_tables, schema_columns

**输出**：`retrieved_context`（完整召回结果，含 tables/columns/values/join_paths）

### 3.7 LSH 预处理与在线查询全流程

> 这是 IR 模块中前置预处理最重、技术栈最独立的部分。整个流程分为**离线构建阶段**和**在线查询阶段**，中间通过 pickle 序列化的索引文件衔接。

#### 3.7.1 离线预处理阶段

离线预处理由 `build_lsh_index.py` 触发，分三步：

##### 步骤 1：提取数据库唯一值（`get_unique_values`）

这一步的目标是：找出数据库中有检索价值的 TEXT 列的唯一值。

**执行过程**：

```
连接 SQLite → 读 sqlite_master 取所有表名
                          ↓
          对每张表用 PRAGMA table_info 取所有列
                          ↓
                 只保留 TYPE 为 TEXT 的列
                          ↓
            过滤掉以下低价值列：
              - 主键列（pk > 0）
              - 列名含 _id、id、url、email、web、time、phone、date、address
              - 列名以 "Id" 结尾
                          ↓
              检查列的数据规模（防止爆炸）：
              - name 列：总长度 < 5,000,000 字符则放行
              - 非 name 列：总长度 > 2,000,000 或 avg_length > 25 → 跳过
              - 唯一值个数 > 10,000 → 跳过（高基数列）
                          ↓
              SELECT DISTINCT 提取所有非 NULL 唯一值
```

**为什么这么多过滤条件**：LSH 索引是内存中的哈希表。如果无差别地把所有列的所有唯一值都加进去，内存会爆炸（想象一个有 100 万行日志的 `message` 列）。过滤的目标是保留那些**用户查询中可能出现的实体值**（人名、地名、产品名等），排除那些不适合做匹配的列（ID、URL、长文本）。

**输出**：一个嵌套字典 `Dict[表名 → Dict[列名 → List[唯一值]]]`。

##### 步骤 2：计算 MinHash 签名并构建 LSH 索引（`build_index`）

对每个唯一值做四件事：

```
唯一值 "Hamilton"
        ↓
  1. 生成 3-gram 集合
     "Ham", "ami", "mit", "ilt", "lto", "ton"  (共 6 个 n-gram)
        ↓
  2. 对每个 n-gram 做 hash，取最小值作为 MinHash 签名
     MinHash(num_perm=128)  →  128 个 hash 值组成的签名向量
        ↓
  3. 生成唯一 key: "drivers_surname_42"
     将 (minhash, table_name, column_name, value) 存入 minhashes 字典
        ↓
  4. 将 key + minhash 插入 MinHashLSH 索引
     MinHashLSH(threshold=0.5, num_perm=128)
```

**关键参数**：

| 参数 | 默认值 | 含义 | 调大 | 调小 |
|------|--------|------|------|------|
| `signature_size` | 128 | MinHash 签名长度（num_perm）| 更精确、更慢、更耗内存 | 更快、省内存、精度下降 |
| `n_gram` | 3 | n-gram 窗口大小 | 适合长文本匹配 | 适合短字符串或中文 |
| `threshold` | 0.5 | LSH 桶判定阈值（Jaccard）| 更精确、可能漏检 | 更高召回、更多误报 |

**MinHashLSH 内部原理（简化版）**：

MinHashLSH 不是把所有签名放进一个桶里比较——那样就是 O(n²) 了。它的做法是：

1. 把 128 个 hash 值分成多个 band（比如 16 个 band，每个 band 8 个 hash）
2. 每个 band 单独 hash，放入对应的桶
3. 两个值的签名**在任何同一个 band 中完全一致** → 它们被分到同一个桶 → 被判定为候选

这样做的效果是：**大概率相似的文档会落入同一个桶，大概率不相似的不会**。这就是"局部敏感"的含义——不需要比较全部，用分桶做剪枝。

**MinHashLSH 的 threshold 参数**：决定了每个 band 的大小（band 越多、每个 band 包含的 hash 越少，threshold 越小）。Jaccard 相似度高于此阈值的两个字符串有很高的概率被分到同一个桶。

##### 步骤 3：持久化到 pickle 文件

```python
preprocessed/lsh_index.pkl      # MinHashLSH 索引对象
preprocessed/minhashes.pkl      # Dict[key → (MinHash, table, column, value)]
preprocessed/unique_values.pkl  # 原始唯一值（调试用，不参与在线查询）
```

这三个文件存储在 `data/{db_id}/preprocessed/lsh/` 目录下。如果 `force_rebuild=False` 且索引已存在，`is_lsh_built()` 会检测到并跳过。

#### 3.7.2 在线查询阶段（`retrieve_values`）

在线查询由 IR 子图的 `retrieve_values` 节点触发，是一个**两阶段流水线**：

```
用户关键词列表（如 ["hamilton", "汉密尔顿", "销售额"]）
          │
          ▼
┌──────────────────────────────────────────────┐
│ 阶段 1: LSH 粗召回                            │
│                                               │
│ 对每个 keyword:                                │
│   1. create_minhash(keyword) → 查询签名        │
│   2. lsh.query(query_mh) → 从桶中取出候选 key  │
│   3. 对每个候选 key 计算精确 Jaccard 相似度      │
│   4. 只保留 jaccard_similarity >= threshold    │
│      （当前 IR 的 lsh_threshold=0.6）           │
│   5. 按相似度降序排序，取 top_k=5              │
│                                               │
│ 输出: 候选列表 [{value, table, column, score}] │
└───────────────────┬──────────────────────────┘
                    │
                    ▼
┌──────────────────────────────────────────────┐
│ 阶段 2: BGE-M3 语义精排                       │
│ （只有 BGE-M3 模型就绪时才执行）                │
│                                               │
│ 对每个候选:                                    │
│   1. 用 BGE-M3 分别 embed keyword 和 value    │
│      embed("hamilton") → 向量 A                │
│      embed("Hamilton") → 向量 B                │
│   2. 计算余弦相似度:                            │
│      cos_sim = dot(A, B) / (|A| * |B|)        │
│   3. 只保留 cos_sim >= value_semantic_threshold │
│      （默认 0.6）                               │
│   4. 最终 score = cos_sim                      │
│                                               │
│ 输出: 精排后的 RetrievedItem 列表               │
│       [{item_type="value", name, table_name,   │
│         score, metadata={lsh_score,            │
│         semantic_score}}]                      │
└───────────────────┬──────────────────────────┘
                    │
                    ▼
           进入 IR assemble 节点
           与 Schema 召回结果合并
```

**没有 BGE-M3 时怎么办**：如果只有 LSH 没有 BGE-M3（或 BGE 模型未加载），阶段 2 被跳过，直接用 LSH Jaccard 分数作为最终 score。此时 `semantic_score=None`。

#### 3.7.3 加载时机（`_prepare_lsh_indexer`）

LSH 索引不是在启动时加载的——它按数据库懒加载。具体发生在 `DbContextPool` 构建 `DbContext` 时：

```
请求到达 → db_pool.acquire("california_schools")
               ↓
DbContext 不存在 → 懒加载
               ↓
_prepare_lsh_indexer("data/california_schools/")
               ↓
检查 preprocessed/lsh/lsh_index.pkl 是否存在
         ↓                        ↓
      存在 → pickle.load         不存在 → 返回 None（打印警告）
         ↓
indexer._loaded_lsh = lsh
indexer._loaded_minhashes = minhashes
         ↓
返回 LSHIndexer 实例（含加载的索引）
```

**注意**：`_prepare_lsh_indexer` 创建 `LSHIndexer` 时用的 threshold=0.3（`db_pool.py:78`），但在 `retrieve_values` 中实际用的是 `self.lsh_threshold=0.6`（`InformationRetrieval.__init__` 传入）。两者的区别是：
- `LSHIndexer(threshold=0.3)`：控制**LSH 桶的分桶阈值**，0.3 意味着 Jaccard>0.3 的两个值有较高概率被分配到同一个桶（更宽松，不漏检）
- `retrieve_values` 的 `lsh_threshold=0.6`：在 LSH 返回候选后，**计算精确 Jaccard 再做一道过滤**（更严格，去噪声）

两道阈值的设计是典型的"粗召回 + 精过滤"模式。

#### 3.7.4 完整流程图

```
┌─────────────────────────────────────────────────────────────────────┐
│                    离线预处理阶段 (build_lsh_index)                  │
│                                                                     │
│  SQLite 数据库                                                      │
│      │                                                              │
│      ▼                                                              │
│  get_unique_values()                                                │
│      │  连接 sqlite_master → 逐表逐列筛选 TEXT 列                    │
│      │  跳过: 主键 / ID / URL / email / time / phone / date         │
│      │  跳过: 总长度>2M / 平均长度>25 / 唯一值>10000                │
│      ▼                                                              │
│  唯一值字典: {"drivers": {"forename": ["Lewis", ...], ...}}          │
│      │                                                              │
│      ▼                                                              │
│  对每个唯一值:                                                      │
│    "Lewis" → char_3gram → ["Lew", "ewi", "wis"]                     │
│            → MinHash(num_perm=128) → 128 维签名                     │
│            → lsh.insert("drivers_forename_0", minhash)              │
│      │                                                              │
│      ▼                                                              │
│  持久化到 pickle:                                                    │
│    preprocessed/lsh/lsh_index.pkl     (MinHashLSH 对象)              │
│    preprocessed/lsh/minhashes.pkl     (签名+元数据字典)               │
│    preprocessed/lsh/unique_values.pkl (原始值, 调试用)               │
└───────────────────┬─────────────────────────────────────────────────┘
                    │
                    │ (启动时懒加载)
                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    在线查询阶段 (IR retrieve_values)                  │
│                                                                     │
│  IR 子图 extract_keywords 产出: ["hamilton", "汉密尔顿", ...]        │
│      │                                                              │
│      ▼                                                              │
│  阶段 1: LSH 粗召回                                                  │
│    keyword="hamilton" → create_minhash → lsh.query(query_mh)        │
│      → 候选: ["Hamilton", "HAMILTON", "Hamil", ...]                 │
│      → 计算精确 Jaccard: keywords vs candidates                      │
│      → 过滤 jaccard < 0.6 → 保留 top 5                              │
│      │                                                              │
│      ▼                                                              │
│  阶段 2: BGE-M3 语义精排（可选）                                     │
│    embed(keyword) vs embed(value) → 余弦相似度                       │
│    → 过滤 cos_sim < 0.6 → 最终 score = cos_sim                     │
│      │                                                              │
│      ▼                                                              │
│  RetrievedItem 列表 → IR assemble 节点                               │
│  与 Schema 召回结果合并 → 注入 JOIN 路径 → RetrievedContext           │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.8 关键技术参数

| 技术 | 实现 | 用途 | 离线/在线 |
|------|------|------|----------|
| BGE-M3 | FlagEmbedding | 列/表描述、用户查询的向量化 | 离线建索引，在线向量化 query |
| ChromaDB | VectorStoreManager | 存储列/表 embedding 并做相似度搜索 | 离线写入，在线查询 |
| LSH | datasketch MinHash | 字段唯一值的近似匹配（分桶剪枝） | 离线建索引，在线查询 |
| MinHash Jaccard | datasketch 内置 | 计算两个字符串的 n-gram 集合相似度 | 在线计算 |
| JOIN 路径 | 从外键元数据推导 | 补充跨表 JOIN 关系 | 预处理时提取，在线注入 |


---

## 4. Clarification 节点

### 4.1 为什么需要澄清

用户在表达查询时经常有歧义。比如"查一下苹果的销量"——"苹果"是指水果还是品牌？IR 召回时可能会召回到不同类型的结果。澄清 Agent 的作用就是在这种时候**主动向用户反问**，明确意图后再继续。

### 4.2 当前实现（Phase 1 占位）

Phase 1（当前版本）Clarification 是空节点：直接设置 `clarification_done=True`，不做任何反向询问。条件边无条件走 ss。

所以当前流程实际上是 IR → SS，没有中间的交互。

### 4.3 Phase 2 规划

- 替换为 `ClarificationAgent.build_graph()` 调用
- 触发条件：IR 召回结果在多维度上有歧义（不同领域的表/列被同时召回）
- 交互方式：SSE 推送反问问题 → 用户回答 → 更新 clarification_history → 继续流程
- 支持多轮：反问后如果仍然模糊，可以再问


---

## 5. SS 子图

### 5.1 为什么需要 SS

IR 召回了宽泛的 schema 候选（可能几十张表、几百列），但实际回答用户查询可能只需要其中的 3-5 个列。把全部信息喂给 CG 既有噪声（多余的列可能让 LLM 生成错误 SQL），又浪费 token（M-Schema 格式化的内容量很大）。

SS 的作用就是**精筛**——只保留最相关的表和列。

### 5.2 子图拓扑

```
to_mschema → evaluate_relevance → filter_columns
```

### 5.3 to_mschema — M-Schema 转换

**做什么**

把 IR 产生的通用表/列结构（`RetrievedContext`）转为 **M-Schema** 格式。

M-Schema 是学术界提出的一种专为 NL2SQL 设计的 schema 表示方式。它不只是列名列表，而是为每列包含了：数据类型、是否为主键/外键、取值范围或枚举值、自然语言描述。这种丰富的元数据使得 LLM 能更准确地理解每列的语义。

**为什么用 M-Schema**

从实践来看，直接传列名给 LLM 生成的 SQL 质量明显不如传 M-Schema。特别是对于非开发者用户，列名可能是内部缩写（如 `prd_ctg_cd`），没有 M-Schema 的描述 LLM 很难理解。

**输入**：`retrieved_context`

**输出**：`mschema_tables`（List[MSchemaTable]）

### 5.4 evaluate_relevance — LLM 列相关性评估

**做什么**

这是 SS 子图中唯一的 LLM 调用。对 M-Schema 格式的每个列，LLM 评估它和用户查询的相关性。评估维度包括：
- 列名是否匹配查询中的实体
- 列的描述是否和查询意图相关
- 列的数据类型是否适合查询中预期的操作（如聚合、排序）

**输入**：`mschema_tables`, `user_query`

**输出**：带相关性评分的 `mschema_tables`

**性能说明**：如果召回结果有 10 张表、50 列，一次评估的 LLM 调用约消耗 2-5s。这是值得的——它让后续 CG 的输入干净很多。

### 5.5 filter_columns — 列过滤

**做什么**

根据相关性评分移除低相关度的列。这步是纯代码逻辑，不需要 LLM。

**过滤规则**：列级别的精度控制。如果一张表的某些列相关而另一些不相关，可以只保留相关的列，而不是整表保留或整表丢弃。

**输入**：评过分后的 `mschema_tables`

**输出**：`selected_schema`（List[MSchemaTable]，这是整个 SS 子图的最终产物）


---

## 6. AnswerabilityCheck 节点

### 6.1 位置与设计意图

AnswerabilityCheck（可回答性检查）放在 SS 之后、CG 之前。为什么放在这？

- **SS 之前**：IR 的召回结果还不够结构化，无法做"是否可回答"的判断
- **SS 之后**：有了 M-Schema 格式的精确 schema，可以判断"在已知的列中能回答吗"
- **CG 之前**：如果不可回答，就避免无意义的 LLM 调用（CG 是最贵的节点之一）

### 6.2 三级判定策略

检查器对每个问题给出三档判定：

| 判定 | 含义 | 行为 |
|------|------|------|
| `answerable="true"` | 有足够信息回答 | 正常进入 cg |
| `answerable="uncertain"` | 不确定是否能回答 | **放行**进入 cg（宽松原则）|
| `answerable="false"` | 在已知 schema 下无法回答 | 结束流程，返回 rejection_reason |

**宽松原则（`strictness="loose"`）**：只有明确不可回答才拦截。不确定时放行，让后续的 CG 和 Decision 来做最终判断。这样做是避免"错杀"——宁可让 LLM 尝试生成后失败，也不要在检查阶段就误判为不可回答。

### 6.3 什么情况会判定不可回答

常见的不可回答场景：
- 用户问的实体在 schema 中不存在（如查"门店客流"但表里没有客流数据）
- 需要的分析维度在 schema 中没有（如查"同比增长率"但表里只有绝对值没有时间维度）
- 问题需要的外部知识当前不可用

### 6.4 rejection_reason 字段

当判定不可回答时，系统会在 `rejection_reason` 中写明原因。这个信息会通过 SSE 推送给前端，让用户明白为什么不能回答。


---

## 7. CG 子图

### 7.1 为什么需要 CG

CG（Candidate Generation）是 SQL 生成阶段。它的目标是：基于用户的查询和选定的 schema，生成 N 个**合法的**候选 SQL。

"合法"在这里有两层意思：
- **语法合法**：通过 sqlglot 解析验证
- **安全合法**：不含 DELETE/UPDATE/DROP 等危险操作

注意是 N 个候选而不是 1 个——这是 Self-Consistency 策略的基础。多个候选通过后续 Decision 的评分选出最佳，比单次生成更可靠。

### 7.2 子图拓扑

```
extract_entities → mask_query → select_few_shot → llm_generate_and_validate
```

各节点的关系是：
1. 先"读"——从查询中提取实体（extract_entities）
2. 再"翻译"——用实体类型替换实体值得到骨架（mask_query）
3. 再"参考"——找相似的 few-shot 帮助理解怎么写（select_few_shot）
4. 最后"生成"——LLM 正式写 SQL（llm_generate_and_validate）

### 7.3 extract_entities — 实体提取

**做什么**

从用户查询中提取命名实体。比如"查询北京地区 2024 年销售额" → 实体为 `["北京", "2024年", "销售额"]`。

**为什么只提取不做类型识别**

实体提取和实体类型识别是两步。这里只做提取，类型识别在 mask_query 中完成。分开的原因是提取用规则+轻量 LLM 调用，类型映射需要结合 schema 信息，放在人更熟悉数据模式的 CG 阶段做。

**输入**：`user_query`

**输出**：`entities`

### 7.4 mask_query — Query 掩码

**做什么**

把实体值替换为它们的类型标签。比如：
```
原 query: "查询北京的销售额"
masked:   "查询{location_name}的{metric_name}"
```

**为什么需要掩码**

掩码后的 query 去掉了具体的实体值，保留了语义骨架。这个骨架可以用来做 few-shot 示例的相似度匹配——不考虑具体值，只看"用户想对什么类型的列做什么操作"。

**输入**：`user_query`, `entities`

**输出**：`masked_query`

### 7.5 select_few_shot — Few-shot 示例选择

**做什么**

从训练集（预构建的 few-shot 库）中选择与当前查询最相似的示例。选择标准是 masked_query 的**骨架相似性**——即语义结构而非字面匹配。

**单表 vs 多表的特殊处理**：如果用户查询涉及多张表（joined tables），只选多表的 few-shot 示例。反之亦然。避免给 LLM 提供不适合当前场景的示例。

**为什么选 few-shot**
- 对于常见查询模式（"按 XX 分组统计 YY 的 ZZZ"），few-shot 示例大大降低了 LLM 生成错误的概率
- 示例的 SQL 写法风格可以作为基线，让 LLM 的输出更一致

**输入**：`masked_query`, `selected_schema`, `is_multi` 标记

**输出**：`few_shots`（List[Dict]，每项包含 query、SQL 和 schema 的完整示例）

### 7.6 llm_generate_and_validate — LLM 生成与安全验证

**做什么**

这是 CG 的核心节点。接收用户查询、schema 和各类辅助信息，调用 LLM 生成 N 个候选 SQL，然后用 sqlglot 做语法验证和安全检查。

**注入到 LLM 的上下文**：

1. **schema_text**（M-Schema 格式）：SS 输出的 schema，包含列名、类型、描述、主外键
2. **用户偏好**：UserMemory 中记录的排序偏好（如默认 DESC）、limit 偏好（如默认 LIMIT 10）、分组偏好
3. **指标定义**：UserMemory 中置信度 >= 0.8 的指标映射。例如"GMV"→"SUM(amount) WHERE status='completed'"，让 LLM 优先使用已知的指标 SQL 模式
4. **历史 SQL 弱参考**（最多 3 条）：SessionMemory v2 召回的历史 SQL，但附带了约束——"仅供写法/指标口径参考，不得使用当前 schema 中不存在的表或列"

**sqlglot 验证**：生成的每段 SQL 必须：
- 能被 sqlglot 解析器正确解析（语法正确）
- 不包含危险 DDL/DML（DELETE、UPDATE、DROP、ALTER、TRUNCATE、INSERT）

**输入**：user_query, schema_text, user_prefs, metrics, historical_refs, few_shots

**输出**：`sql_candidates`（List[SQLCandidate]，最多 N 个，N 默认 3）

### 7.7 异常处理

- LLM 调用失败 → 返回空 list，主图会走 cg 条件边到 END
- 所有候选都被 sqlglot 过滤掉 → 同上
- LLM 返回的 JSON 格式不对 → `parse_json` 会尝试修复，修复失败返回空


---

## 8. Execution 节点

### 8.1 为什么叫 ExecuteAll（决策 51）

Execution 节点在决策 51 中做了重大简化——不再做 LLM 修复，而是**一次性执行所有候选**。

原来（决策 51 之前）的逻辑是：执行 → 失败 → LLM 修复 → 重执行，这个循环在每个候选上做，非常耗时。后来发现修复逻辑在 Decision 的 SmartFix 阶段做更合理——因为只有 Decision 知道哪个候选值得修。

所以现在的分工是：
- **Execution**：只管执行，不修复。快速拿到所有候选的执行结果
- **Decision**：基于执行结果做评分，如果分数不够高再进入 SmartFix 修复

### 8.2 缓存命中分支

当 HistoryCache 命中时，Execution 的逻辑完全不同：

1. 从 `cached_sql` 构造一个 `SQLCandidate`（id="cache_hit"）
2. 仅执行这个单候选
3. 不走 IR/SS/CG 的完整链路

这就是"缓存命中绕过后半段"的含义——从 history_cache 直接跳到 execution，省掉了 IR→SS→CG 的所有 LLM 调用。

此时 execution 仍然是"一次性执行"，只是候选集只有一条。

### 8.3 标准执行分支

对于正常流程（cache_miss），Execution 会：
1. 接收 CG 产出的 `sql_candidates`（正常 3 个候选）
2. 准备好 schema_text（给 SmartFix 用的 schema 文本格式，只在有 schema 时才生成）
3. 逐个候选调用 `executor.execute(cand.sql)`
4. 把执行结果写回候选的字段（result、execution_time、status、error_message、structured_error）

### 8.4 执行结果的结构

每个 SQLCandidate 执行后更新以下字段：

| 字段 | 成功时 | 失败时 |
|------|--------|--------|
| `result` | 查询结果（list of dict/row）| 不变 |
| `execution_time` | 执行耗时（秒）| 不变 |
| `status` | SUCCESS | FAILED |
| `error_message` | None | 错误文本 |
| `structured_error` | None | `ExecutionError`（含 error_type、severity）|

`structured_error` 很重要——它包含了错误类型分类（如 `SYNTAX_ERROR`、`TABLE_NOT_FOUND`、`TIMEOUT`），Decision 的 SmartFix 会根据错误类型决定修复策略。

### 8.5 边缘情况

- 所有候选都失败：Execution 仍正常返回，不会报错。决策由 Decision 的全失败分支处理
- schema_text 生成失败：降级为空字符串，SmartFix 时没有 schema 参考可能导致修复效果下降
- 缓存命中但 cached_sql 为空：返回 error（这种情况理论上不应该发生）


---

## 9. Decision 子图

### 9.1 为什么需要这个复杂的决策过程

NL2SQL 的一大难题是：SQL 生成是"有标准答案的开放问题"——同一个查询可以用多种 SQL 表达，但只有少数是正确的。LLM 单次生成可能犯错，所以我们需要：

1. **生成多个候选**（CG 阶段已做）
2. **从多个候选中选出最好的**（Decision 的核心）

选最好的不是简单说"看谁跑得快"——结果正确性远比执行速度重要。所以 Decision 设计了两轮评分（看结果、看 SQL）+ 修复循环的多层过滤机制。

### 9.2 子图拓扑

```
filter → [条件] → score_r1 → [条件] → finalize_r1 → assemble → verify → END
                               │                (路径 A)
                               ├→ score_r2 → finalize_r2 → assemble → verify
                               │                (路径 B/C)
                               └→ pick_for_fix → smart_fix → assemble → verify
                                   (路径 D/E)
                       → all_failed → assemble → verify (路径 F/G/H)
```

### 9.3 filter — 候选取舍

这是决策子图的第一个节点。把 `candidates` 分为两组：
- `success_candidates`：执行成功的候选
- `failed_candidates`：执行失败的候选

路由逻辑很直观：
- **有成功候选** → 进入 score_r1 评分流程
- **全部失败** → 进入 all_failed 全失败修复流程

### 9.4 R1: score_by_data — 数据视角评分

**评分方式**

R1 只看结果数据，不看 SQL 代码。LLM 会收到：
- 候选 ID
- 执行时间
- 返回行数
- 列名
- **前 20 行数据预览**（每个单元格最多 20 字符，超出截断加 "..."）

**为什么只给 20 行 + 截断**

这是 token 成本和质量之间的平衡。显示全部结果行可能消耗数千 token，而 20 行截断预览已经能让 LLM 判断"这个结果是否回答了用户的问题"。对于大多数查询，前 20 行足以看出结果的结构和内容倾向。

**评分标准（0-5 分）**：
- 5 分：结果完全符合用户查询意图
- 3-4 分：部分符合，但可能有偏差
- 1-2 分：只有少量匹配
- 0 分：完全不相关或错误

**输入**：成功候选的结果数据预览

**输出**：`List[{candidate_id, score(0-5), reason}]`

### 9.5 route_after_r1 — R1 后路由

R1 评分的结果直接决定了后续流程的路径选择。这是整个决策子图中最关键的分支点：

| R1 结果 | 路径 | 后续 |
|---------|------|------|
| 唯一候选 score=5 | **A** | 直选，走 verify 收尾 |
| >=2 个候选 score=5（并列满分）| **B/C** | 触发 R2 SQL 视角评分打破平局 |
| 最高分 < 5 | **D/E** | 选最高分候选送入 SmartFix 修复 |
| R1 评分失败/空 | **D/E** | 兜底选第一个候选送入 SmartFix |

#### 路径 A：直选（最理想的情况）

当且仅当**恰好一个候选**拿到了数据视角满分 5/5。这说明：
- 这个候选的结果**无疑问地**回答了用户的问题
- 不需要再看 SQL 写法是否漂亮
- 不需要尝试修复

直接走 `finalize_r1` → `assemble_decision` → `verify`，这是最快、最理想的分支。

#### 路径 B/C：R2 SQL 评分（平局打破）

当多个候选在数据视角都是满分 5/5，说明它们的**结果看起来都对**。但 SQL 写法可能差异很大——可能有的写法高效、有的写法脆弱、有的用了正确的聚合口径而有的只是碰巧结果对了。

R2 就是用来做这个"平局打破"的。

### 9.6 score_by_sql — R2: SQL 视角评分

**触发条件**：R1 有 >=2 个候选并列 score=5

**R2 的输入不包含结果数据**——这是有意设计的。R2 评价的是"哪个 SQL 写得更好"，而不是再次看结果是否匹配。评价标准：
- SQL 是否使用了正确的 JOIN 类型和条件
- 聚合逻辑是否准确（比如用了 SUM 还是 COUNT，GROUP BY 是否正确）
- 写法是否简洁高效
- 是否合理使用了 WHERE 条件

**为什么 R2 收窄到仅 R1 并列的候选**

不是所有候选都进入 R2——只有在 R1 并列 5 分的那些候选进入 R2。这样做可以节省 token（R2 的 LLM 调用不需要看所有候选，只看最顶尖的几个）。

**R2 后的路由**：
- **路径 B**：R2 打出了唯一的最高分 → 选该候选
- **路径 C**：R2 仍然并列（极少见）→ 选 execution_time 最短的

### 9.7 pick_for_fix — 选定修复候选

当 R1 最高分 < 5 时（路径 D/E），说明所有候选的数据结果都不完美，无法直接接受。这时系统会做两件事：
1. 从评分结果中选出**最高分**的那个候选
2. 送入 SmartFix，尝试修复它

**为什么只选一个修复**

避免"广撒网"式修复浪费 LLM 调用。如果 3 个候选分别得了 4 分、2 分、1 分，去修 4 分的那个效率最高。

### 9.8 smart_fix — SmartFix 单候选修复

**做什么**

对选中的候选执行最多 3 轮的修复循环：

```
第 1 轮: 传原始 SQL + 错误信息给 LLM → LLM 修正 → 执行 → 成功? → 结束
                                                              → 失败 → 第 2 轮
第 2 轮: 传第 1 轮的错误 + 修正历史给 LLM → 重复...
第 3 轮: 同上，但这是最后一轮
失败 → 标记 fix_failed=true
```

**修复条件**
- 需要 `SQLFixLoop` 实例（含 executor 和 llm_client）
- 如果 `SQLFixLoop` 不可用，会用 `SelfConsistencyDecision` 的 llm_client 兜底
- 如果都没有 → 标记 fix_failed

**路径 D vs E**
- **D**：SmartFix 在 3 轮内成功 → 使用修复后的 SQL + 新执行结果
- **E**：SmartFix 3 轮全部失败 → 保留原始 SQL（无执行结果），标记 fix_failed

### 9.9 all_failed — 全失败分支

当所有候选都在执行阶段失败了（没有一个执行成功），进入全失败分支。

**错误分级（ERROR_SEVERITY）**：不同类型的执行错误有不同的"严重级别"：
- **轻级错误**（如超时 OUT 错误）：可能是资源问题，值得重试修复
- **中级错误**（如表不存在 TABLE_NOT_FOUND）：可能 SQL 的问题，可以尝试修复
- **重型错误**（如权限问题 PERMISSION_DENIED）：通常无法修复

**挑选最轻一级候选**：系统会找到所有失败候选中错误最轻的那个级别，取该级别下的全部候选，逐个尝试 SmartFix。

- **路径 F**：任一候选修复成功 → 使用修复结果
- **路径 G**：所有候选都修不好 → 拒答
- **路径 H**：最轻级别属于不可修类型（`UNFIXABLE_ERRORS`）→ 直接拒答，不尝试修复

### 9.10 assemble_decision — 构造决策结果

综合所有阶段的结果，构造 `DecisionResult`：

```python
@dataclass
class DecisionResult:
    selected_sql: str                     # 最终选定的 SQL
    selected_result: Any                  # 执行结果数据
    execution_time: float                 # 执行耗时
    decision_reason: str                  # 决策理由（包含路径说明）
    voting_summary: Dict[str, Any]        # 投票摘要（路径、分数、并列情况）
    candidate_scores_r1: List[Dict]       # R1 评分明细
    candidate_scores_r2: Optional[List]   # R2 评分明细（如有）
    selected_candidate_id: Optional[str]  # 选中候选 ID
    fix_failed: bool                      # SmartFix 是否失败
    fix_rounds_used: int                  # 实际修复轮次
    last_error: Optional[str]             # 最后错误信息
    decision_path: str                    # 路径标识 A-H
```

decision_path 的值可以直接用来追踪和理解系统的决策行为——看到路径 A 就知道是最佳情况，看到路径 H 就是最差情况。

### 9.11 verify — 结果可信度验证

**做什么**

对最终选定的结果做一次**严格验证**（决策 24）。`ResultVerifier` 会：
1. 看结果是否真的回答了用户的问题
2. 检查结果中是否有明显的不合理之处（如空值太多、数据范围异常）
3. 如果发现结果不可信 → 标记 `should_reject=true`，追加到 rejection_reason

**为什么在 Decision 末尾再做一次验证**

前面有 AnswerabilityCheck 在 CG 前做了"问题是否可回答"的判断，但那是基于 schema 的。实际执行出来的结果可能完全不同——比如查询成功但返回了空结果集，或者返回了明显不符合用户预期的数据。所以需要在最终输出前再做一次结果级别的验证。

### 9.12 8 条路径总结

| 路径 | 触发条件 | 过程 | 结果 |
|------|---------|------|------|
| **A** | R1 唯一 =5 | R1 评分 → 直选 → verify | ✅ 正常返回 |
| **B** | R1 并列=5 → R2 决出唯一 | R1 评分 → R2 评分 → 选最高 → verify | ✅ 正常返回 |
| **C** | R1 并列=5 → R2 仍并列 | R1 评分 → R2 评分 → 选最快 → verify | ✅ 正常返回 |
| **D** | R1 <5 → SmartFix 成功 | R1 评分 → 选最高分 → SmartFix(≤3轮) → verify | ✅ 用修复后 SQL 返回 |
| **E** | R1 <5 → SmartFix 失败 | R1 评分 → 选最高分 → SmartFix(3轮全失败) → verify | ⚠️ 返回原始 SQL，无结果 |
| **F** | 全失败 → SmartFix 最轻级成功 | 错误分级 → 逐个尝试修复 → 任一成功 → verify | ✅ 用修复后 SQL 返回 |
| **G** | 全失败 → SmartFix 最轻级失败 | 错误分级 → 全部修不好 → verify | ❌ 拒答 |
| **H** | 全失败 → 最轻级不可修类型 | 错误分级 → 全部不可修 → verify | ❌ 拒答 |

**在实践中，绝大多数成功查询走的是路径 A，少数复杂查询可能走 B/C/D，而 F/G/H 代表了系统在尽力后仍然失败的情况，应该出现在较少的边界场景中。**


---

## 10. Memory Update 节点

### 10.1 为什么放在最后

记忆更新的逻辑很简单：从本轮查询的结果中提取**用户的行为模式**，存起来供以后使用。

放在最后是因为所有必需的字段都已经确定：
- `final_sql` 已确定（最终 SQL）
- 执行结果已确定（成功/失败）
- 所有评分和决策已完成

### 10.2 6 项更新内容

#### 10.2.1 常用表（_update_table_usage）

从 `final_sql` 中用正则 `(FROM|JOIN)\s+(\w+)` 提取表名，记录到 `frequently_used_tables`。

每用一次，该表的 `query_count` 加 1，`last_used` 更新为当天日期。

长期来看，经常被查的表的 count 会越来越高。这些信息可以被 SS 阶段用来做表优先级排序。

**为什么不用 sqlglot 提取表名**

正则足够快，对于 FROM/JOIN 后的表名提取准确率接近 100%。用 sqlglot 解析再提取增加复杂度，必要性不大。

#### 10.2.2 指标定义（_update_metric_definitions）

检测 `final_sql` 中是否包含聚合函数（SUM/COUNT/AVG/MAX/MIN），如果有则尝试提取指标定义。

**双轨制**：
- **auto_learned**（自动学习）：从 SQL 中自动提取，初始 confidence=0.5，每多用一次 +0.1，上限 0.9
- **user_taught**（用户主动教）：confidence=0.95，**不会被 auto_learned 覆盖**

**LLM 提取 vs 简单规则**：如果有 LLM 客户端，用 LLM 提取指标名和描述。如果 LLM 不可用（或调用失败），回退到简单规则——取聚合函数名+参数列名作为指标名（如 `SUM_amount`）。

#### 10.2.3 查询偏好（_update_query_preferences）

从 `final_sql` 中检测：
- **排序偏好**：ORDER BY ... DESC → `default_sort=DESC`；ORDER BY ... ASC → `default_sort=ASC`
- **分组粒度**：GROUP BY ... year/month/day → `default_group_by=daily`
- **LIMIT 偏好**：LIMIT ≤20 → `default_limit=具体数值`

这些偏好会在 CG 阶段注入给 LLM，使生成的 SQL 更贴合用户的习惯。

#### 10.2.4 会话上下文（_update_session_context）

更新 SessionMemory 的上下文摘要，包括：
- `last_topic`：当前查询的前 20 个字符
- `last_tables`：SQL 中涉及的表名列表
- `last_time_range`：查询中检测到的年份（如果有）

这些摘要用于会话记忆的快速回顾。

#### 10.2.5 澄清历史（_update_clarification_history）

把本轮查询的 `clarification_history`（如果有）写入 UserMemory 的 `clarification_history` 列表。这保证了即使在跨 session 的情况下，也能回溯用户和管理员的问数交互过往。

#### 10.2.6 SessionMemory v2 写入（_update_session_recall_memory）

只有在满足严格条件时才写入 SessionMemory v2 召回库：
1. `final_sql` 非空
2. 没有 `error` 或 `rejection_reason`
3. 结果验证通过（`should_reject=false`）
4. 有成功候选或缓存命中

写入的数据包括 query embedding（到 Chroma）和完整对话记录（到 JSON Conversation Store）。具体见第 11 章的 SessionMemory v2 说明。


---

## 11. 记忆系统

### 11.1 三层记忆架构

NL2SQL 的记忆系统分为三层，每层的**作用域**和**用途**都不同：

```
作用域: session 级                         作用域: session 级                     作用域: user 级
短期记忆                                  混合召回记忆                          长期偏好记忆
┌──────────────────────┐              ┌──────────────────────┐              ┌──────────────────────┐
│  SessionMemory v1    │              │  SessionMemory v2    │              │  UserMemory           │
│                      │              │                      │              │                      │
│  存储: JSON 文件      │              │  索引: Chroma        │              │  存储: JSON 文件      │
│  位置: data/sessions/│              │  存储: JSON 文件      │              │  位置: data/user_memory│
│                      │              │  位置: data/session_  │              │                      │
│  用途: 会话历史回顾,  │              │  memory_v2/          │              │  用途: 跨 session     │
│  HistoryCache 判断    │              │                      │              │  用户偏好记忆         │
│                      │              │  用途: 当前 session   │              │  6 大预定义 topic     │
│  内容: 最近 N 轮对话   │              │  内历史 query 混合召回 │              │                      │
└──────────────────────┘              └──────────────────────┘              └──────────────────────┘
```

这三层互不冲突，各司其职：
- **SessionMemory v1**：给 HistoryCache 用的会话历史上下文
- **SessionMemory v2**：给 IR/CG 用的历史 query 语义召回
- **UserMemory**：跨 session 的用户行为模式积累

### 11.2 SessionMemory v1 — 会话管理器

最简单的层级。`SessionManager` 以 JSON 文件形式存储每个 session 的最近 N 轮对话内容。用于：
- `GET /api/v1/session/{session_id}` 接口查询历史
- HistoryCache 判断时提供对话上下文
- IR 节点提取 follow-up 查询时提供上轮信息

存储位置：`data/sessions/{session_id}.json`

### 11.3 SessionMemory v2 — 混合召回

这是最复杂的层级，也是"memory"这个词在 NL2SQL 中最具技术含量的部分。

#### 11.3.1 为什么需要 v2

v1 只能做"精确匹配"——按 session_id 取最近几轮对话。但很多历史查询的价值在于**语义相似**而非字面相同。用户不可能每次都问一模一样的句子，但可能问不同句子表达同一个意思。v2 就是为了解决这个问题——通过向量语义检索找到"意思相近"的历史查询。

#### 11.3.2 两层存储结构

SessionMemory v2 把数据存在两个地方：

```
成功查数的 query
      │
      ├──→ Query Recall Index (Chroma)
      │     存: query embedding + 过滤元数据(user_id/session_id/db_id/success=true)
      │     用途: 快速语义检索，精确过滤
      │
      └──→ Conversation Store (JSON)
            存: user_query + final_sql + 时间戳（无结果数据、无中间状态）
            用途: 召回命中后回表加载完整内容
```

**为什么分两层**：
- Query Index 小而快（只存 embedding + 少量元数据），适合大量候选的检索
- Conversation Store 大但全（存完整对话关键信息），只在索引命中且通过 RRF 阈值后才去读

**不存结果数据、不存 LLM thinking、不存中间 graph state**：
- 结果数据太大，存它没有意义（历史结果不一定反映当前数据）
- LLM thinking 和 graph state 是临时产物，只对当前请求有意义

#### 11.3.3 召回流程：过滤 → 检索 → 融合 → 回表

```
用户当前 query: "查询苹果的销售额" (user_id=u1, session_id=s1, db_id=db1)
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ Step 1: 过滤                                                │
│ 只考虑: user_id=u1 AND session_id=s1 AND db_id=db1         │
│         AND success=true                                    │
│ 结果: 从几十条历史中筛选出 5 条匹配条件的历史                 │
└─────────────────────────┬───────────────────────────────────┘
                          │
              ┌───────────┴───────────┐
              ▼                       ▼
┌──────────────────────┐  ┌──────────────────────┐
│ Step 2a: Dense 召回  │  │ Step 2b: BM25 召回    │
│ Chroma 向量语义检索   │  │ 本地 BM25 文本检索    │
│ top_k=10             │  │ top_k=10             │
│                      │  │                      │
│ 对当前 query 做       │  │ 对当前 query 做      │
│ BGE-M3 embedding →   │  │ 中英文 tokenizer →   │
│ 向量余弦相似度排序    │  │ TF-IDF 分数排序       │
└──────────┬───────────┘  └──────────┬───────────┘
           │                         │
           └──────────┬──────────────┘
                      ▼
┌─────────────────────────────────────────────────────────────┐
│ Step 3: RRF 融合排序                                        │
│                                                            │
│ 对每个在任一检索通道中出现的历史 query:                       │
│   rrf_score = 1/(60 + dense_rank) + 1/(60 + bm25_rank)     │
│                                                            │
│ 只保留 rrf_score >= 0.015 的结果                             │
│ (允许单路命中，require_multi_channel_hit=false)              │
└─────────────────────────┬───────────────────────────────────┘
                          │
                      ┌───┴───┐
                      ▼       ▼
                  score≥threshold  score<threshold
                      │              (丢弃)
                      ▼
┌─────────────────────────────────────────────────────────────┐
│ Step 4: 回表加载 Conversation Store                         │
│                                                            │
│ 用 conversation_id + turn_id 去 JSON 文件中读取完整对话内容   │
│ 优先用 Conversation Store 中的数据（比 Chroma metadata 准）   │
└─────────────────────────┬───────────────────────────────────┘
                          │
                      ┌───┴───┐
                      ▼
          ┌──────────────────────┐
          │ HistoryCache 判断     │
          │                      │
          │ 可复用?               │
          │   ├── 是 → 走缓存命中 │
          │   └── 否 → 作为弱参考 │
          │          注入 CG 阶段 │
          └──────────────────────┘
```

#### 11.3.4 HybridSessionRetriever

这是整个召回流程的入口。`retrieve()` 方法：
1. 调 `query_index.query_dense()` 做 Chroma 向量召回（异常时降级为空）
2. 调 `bm25_retriever.query()` 做本地 BM25 召回（异常时降级为空）
3. 调 `rrf_ranker.fuse()` 做 RRF 融合排序
4. 对通过的 ref 调 `conversation_store.load_turn_window()` 回表加载

**安全降级**：dense 或 BM25 任一通道失败时，系统**不崩溃**——失败通道返回空列表，只用另一通道的结果做 RRF（RRFRanker 天然支持单路命中）。

### 11.4 UserMemory — 用户长期记忆

#### 11.4.1 六大预定义 Topic

UserMemory 不是自由格式的 JSON——它有严格的固定 schema。设计这样做的原因是：LLM 如果不加约束地往记忆里塞东西，很快就会充满各种噪声。固定 topic 让系统知道"哪里该存什么"。

| Topic | 类型 | 默认值 | 记录内容举例 |
|-------|------|--------|------------|
| `term_preferences` | dict | `{}` | `{"苹果": {"resolved_to": "fruit_apple", "confidence": 0.9}}` |
| `frequently_used_tables` | dict | `{}` | `{"sales": {"query_count": 5, "last_used": "2026-06-25"}}` |
| `metric_definitions` | dict | `{}` | `{"GMV": {"description": "完成订单金额总和", "sql_pattern": "SUM(...)", "confidence": 0.7}}` |
| `query_preferences` | dict | `{}` | `{"default_sort": "DESC", "default_limit": "10"}` |
| `domain_context` | dict | `{}` | `{"industry": "生鲜电商", "department": "运营部"}` |
| `clarification_history` | list | `[]` | `[{"question": "您指的是哪个产品？", "answer": "苹果"}]` |

#### 11.4.2 Schema 治理规则

**固定顶层 Key**：只有 6 个 topic + `user_id`/`created_at`/`updated_at` 允许作为顶层 key。任何其他 key 在保存时会被自动移除。

**污染字段自动过滤**：以下 key 在任何层级都会被拦截，不会进入长期记忆：
- few-shot 相关：`few_shot`, `few_shots`, `few_shot_examples`, `examples`, `sql_examples`
- 结果数据：`final_result`, `result`, `result_rows`, `execution_results`
- 中间状态：`llm_thinking`, `graph_state`, `intermediate_state`

**规范化**：加载时自动补充缺失 topic、移除未知 key。保存时会再做一次规范化。所以即使外部直接修改了 JSON 文件，也有一层保护。

#### 11.4.3 安全边界

- 进程 LRU 缓存，上限 100 个用户（API 层的 `_user_memory_cache`）
- 每个用户的记忆文件独立存储在 `data/user_memory/{user_id}.json`
- 原子读写（Storage 的 `atomic_read`/`atomic_write`），防止并发写入损坏

### 11.5 HistoryCache — 历史命中检测

#### 11.5.1 地位

HistoryCache 虽然在代码里放在 `src/memory/` 下，但它在主图流程中**不是 memory 节点**——它是 `history_cache` 节点，**排在 IR 之前**，是整个流水线的第一道关卡。

#### 11.5.2 判断逻辑

HistoryCache 的核心是 `check()` 方法。它用 LLM 判断当前查询能否用历史 SQL 回答。

**输入**：
- `session_history`：SessionMemory v1 的最近几轮 + SessionMemory v2 召回的 recalled_history
- `metric_definitions`：UserMemory 中的指标定义（如 "GMV" 有标准 SQL 模式）

**判断过程**：
1. 把 session_history 和 metric_definitions 格式化为文本
2. 调 LLM（使用 `CACHE_CHECK_PROMPT`），问"这个查询能用历史 SQL 或已有指标回答吗？"
3. LLM 返回 JSON：`{can_reuse, cached_sql, source, confidence}`
4. 系统做安全边界检查

**注意**：history_cache 和 IR/SS/CG 调的是**同一个 LLM**，但 prompt 不同。这里用的细节是 `thinking=False`——历史缓存检测不需要思考链，规则明确、输出固定。

#### 11.5.3 安全边界

即使 LLM 认为可以复用，系统也额外做三道检查：
1. **置信度门槛**：`confidence < 0.8` → 不复用（走全链路）
2. **SQL 必填**：如果 LLM 没给出具体的 cached_sql → 不复用
3. **时间变化**：涉及时间变化的 follow-up 不推荐复用（prompt 中已有说明）

#### 11.5.4 缓存的 SQL 来源

HistoryCache 能复用的 SQL 有两个来源：
- **session_history**（会话历史）：当天在这个 session 里执行成功的 SQL
- **metric_definitions**（指标定义）：UserMemory 中用户教过或自动学习的指标 SQL 模式

cache_hit 后走 execution 节点重新执行（不是直接用历史结果），确保数据的时效性。


---

## 12. API 层

### 12.1 服务启动流程

```
python run_api.py [--port 8080] [--db_id california_schools]
    │
    ▼
app.py lifespan startup
    │
    ▼
deps.init_globals(data_dir)
    │
    ├── Step 1: 加载 BGE-M3 模型
    │   加载到 CPU，全局单例。加载耗时约 10-30s（取决于模型文件位置）。
    │
    ├── Step 2: 初始化 Chroma VectorStore
    │   连接预构建的 Schema 向量索引。如果没有索引，打印警告但不中断启动。
    │
    ├── Step 3: 创建 LLM 客户端
    │   封装 Qwen DashScope API，支持 invoke/stream/thinking 等调用方式。
    │
    ├── Step 4: 构造各 Agent 实例
    │   ├── SQLGenerator(num_candidates=3)
    │   ├── SelfConsistencyDecision(llm_client + result_verifier)
    │   ├── AnswerabilityChecker(strictness="loose")
    │   ├── HistoryCache(min_confidence=0.8)
    │   └── MemoryUpdater(llm_client + session_retriever)
    │
    ├── Step 4.5: 构造 SessionMemory v2 召回组件
    │   ├── SessionRecallConfig（从环境变量读取配置）
    │   ├── JsonConversationStore（data/session_memory_v2/）
    │   ├── ChromaSessionQueryIndex（共享 Chroma persist 目录）
    │   ├── LocalBM25Retriever
    │   ├── RRFRanker(k=60, threshold=0.015)
    │   └── HybridSessionRetriever（前几项的组合）
    │
    ├── Step 5: 初始化 SessionManager
    │   data/sessions/，LRU 缓存上限 200
    │
    ├── Step 6: 装配 Globals dataclass
    │   所有全局单例的容器
    │
    └── Step 7: 初始化 DbContextPool
        max_size=2（默认），LRU 淘汰；每个 db 的 DbContext 按需懒加载

    ──→ 服务就绪，监听端口
```

**重要设计点**：
- BGE-M3 和 LLM Client 是全局单例，不按数据库重复加载
- DbContext（含一次性的 SQLAlchemy engine + 预构建索引）按 db_id 懒加载，用 LRU 淘汰
- `--db_id` 参数可以预加载某个数据库的 DbContext，避免首次请求慢
- 如果 Chroma 索引不存在，服务仍可启动，但后续 IR 阶段会因为没有向量召回而效果下降

### 12.2 请求生命周期

```
POST /api/v1/query  {"query": "查询销售额", "session_id": "s1", "user_id": "alice", "db_id": "db1"}
    │
    ├─ 1. 路由层: query_router.create_query()
    │    生成 query_id = uuid4().hex[:12]
    │
    ├─ 2. 获取 SessionMemory（按 session_id）
    │    从 SessionManager 获取当前会话的对话历史（SessionMemory v1）
    │    SessionMemory v2 的 QueryRecallIndex 在 DB 初始化阶段启动
    │
    ├─ 3. 获取 UserMemory（按 user_id）
    │    从进程 LRU 缓存中获取（上限 100），未命中则从文件加载
    │
    ├─ 4. 获取 metric_definitions
    │    从 UserMemory.get_metric_definitions(min_confidence=0.7) 获取
    │
    ├─ 5. 构造 NL2SQLState
    │    注入 user_query、user_id、session_id、db_id、query_id
    │    注入 _user_memory、_session_memory 实例（graph 节点内部使用）
    │    设置 conversation_history（最近 N 轮）
    │    设置 metric_definitions
    │
    ├─ 6. 获取 DbContext
    │    pool.acquire(db_id) → 按需懒加载该数据库的 engine + 索引
    │    加载后用 pool.release(db_id) 归还
    │
    ├─ 7. 构造主图
    │    build_main_graph() 传入当前 DbContext 的各 Agent 实例
    │    → 编译 LangGraph（StateGraph.compile()）
    │
    ├─ 8. graph.invoke(initial_state)
    │    执行完整流水线（≈ 10-60s 取决于查询复杂度和 LLM 响应速度）
    │    → 流式 SSE 推送沿途所有事件
    │
    └─ 9. SSE 流式响应
        ├── stage: IR started/done
        ├── stage: SS started/done
        ├── stage: CG started/done
        ├── stage: Execution started/done
        ├── stage: Decision started/done
        ├── result: 最终 SQL + 数据
        ├── done: has_result=true/false
        └── [error]: 如果执行过程中出错
```

### 12.3 DbContext 的生命周期

DbContext 按 db_id 隔离的。当请求指定 `db_id: "california_schools"` 时：
1. `pool.acquire("california_schools")` 检查池中是否有该 db 的 instance
2. 没有 → 懒加载：创建 SQLAlchemy engine、加载 LSH 索引、获取表元数据
3. 返回 DbContext → 请求使用 → `pool.release(db_id)`
4. 如果池满（默认 max_size=2），LRU 淘汰最近使用的 DbContext

同一个 db 的连续请求会复用池中的引擎和索引，避免了反复创建连接的开销。

### 12.4 API 端点完整说明

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/health` | GET | 健康检查 + DbContextPool 状态（在线 db 列表、池使用情况）|
| `/api/v1/query` | POST | 核心查询接口。接收 `{query, session_id, user_id, db_id}`，返回 SSE 流式响应 |
| `/api/v1/databases` | GET | 获取所有已接入的数据库列表（从 data/preprocessed/ 扫描）|
| `/api/v1/databases/{db_id}/tables` | GET | 获取指定数据库的表清单（列名、类型、描述）|
| `/api/v1/session/{session_id}` | GET | 获取会话历史（最近 N 轮对话）|
| `/api/v1/user/{user_id}/memory` | GET | 获取用户记忆（6 个 topic 的完整内容）|
| `/api/v1/user/{user_id}/metrics` | GET | 获取用户指标定义（置信度 >= 0.7 的指标）|


---

## 13. SSE 流式事件

### 13.1 为什么要用 SSE

NL2SQL 的单次查询耗时通常在 10-60 秒之间。如果使用传统 RESTful 接口，客户端需要一直等待直到请求完成——期间没有任何反馈，用户体验很差。

SSE（Server-Sent Events）让服务器可以在请求处理过程中**持续推送事件**给客户端。客户端可以实时看到：
- 当前执行到哪个阶段（stage）
- 关键词提取的结果（keywords）
- Schema 召回的结果（schema_recall）
- LLM 的思考链（llm_thinking）
- 候选 SQL（sql_candidates）
- 执行进展（execution）
- 评分结果（score_r1 / score_r2）

### 13.2 事件类型与推送时机

| 事件类型 | 推送时机 | 关键数据 |
|----------|---------|---------|
| `stage` | 每个节点 start/done | `{node, status, error?}` |
| `cache_check` | HistoryCache 完成 | `{hit, source, confidence, cached_sql, recalled}` |
| `keywords` | IR 关键词提取完成 | `{groups: [{name, expansions}]}` |
| `schema_recall` | IR 召回完成 | `{groups: [{name, top_columns}]}` |
| `llm_thinking` | LLM 思考链实时推送 | 文本片段 |
| `answerability` | 可回答性检查完成 | `{answerable, confidence, reason}` |
| `sql_candidates` | CG 生成完成 | `{candidates: [{id, sql}]}` |
| `execution` | 每条候选执行完成 | `{candidate_id, success, rows, error}` |
| `score_r1` | R1 评分完成 | `{scores: [{candidate_id, score, reason}]}` |
| `score_r2` | R2 评分完成（仅 R1 并列时）| `{scores: [{candidate_id, score, reason}]}` |
| `final_decision` | Decision 完成 | `{selected_id, selected_sql, path, fix_failed, reason}` |
| `result` | 最终结果可用 | `{sql, result, query_id}` |
| `error` | 任意节点异常 | `{node, error}` |
| `done` | 整条查询完成 | `{has_result, query_id}` |

### 13.3 SSE 格式示例

```
data: {"type": "stage", "data": {"node": "ir", "status": "started", "query_id": "abc123def456"}}

data: {"type": "keywords",
       "data": {"groups": [
           {"name": "销售额", "expansions": ["gmv", "sales"]},
           {"name": "苹果", "expansions": ["apple", "fruit_apple"]}
       ], "query_id": "abc123def456"}}

data: {"type": "stage", "data": {"node": "ir", "status": "done", "query_id": "abc123def456"}}

data: {"type": "stage", "data": {"node": "ss", "status": "started", "query_id": "abc123def456"}}

data: {"type": "result",
       "data": {"sql": "SELECT SUM(amount) FROM sales WHERE product='Apple'",
                "result": [{"SUM(amount)": 12345}],
                "query_id": "abc123def456"}}

data: {"type": "done", "data": {"has_result": true, "query_id": "abc123def456"}}
```

### 13.4 客户端注意事项

- SSE 是 HTTP 长连接，客户端不要关闭读取超时
- 服务器每 15 秒发送一次 heartbeat 保活（`SSE_HEARTBEAT_INTERVAL`）
- 每个 event 的数据是一行 JSON，以 `data: ` 开头
- 事件之间用空行分隔（SSE 协议标准）
- `error` 事件不意味着流程一定会结束——有些节点的错误（如 IR 召回异常）会被降级处理


---

## 14. 配置参考

### 14.1 环境变量速查

| 环境变量 | 默认值 | 必填 | 说明 |
|----------|--------|------|------|
| **LLM 配置** | | | |
| `QWEN_API_KEY` | — | ✅ | 通义千问 DashScope API 密钥 |
| `QWEN_BASE_URL` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | ❌ | 兼容 OpenAI 协议的 API 端点 |
| `QWEN_MODEL` | `qwen3.6-plus` | ❌ | 模型名称 |
| `LLM_ENABLE_THINKING` | `true` | ❌ | 是否启用思考链 SSE 推送 |
| **Embedding 配置** | | | |
| `BGE_M3_MODEL_PATH` | `BAAI/bge-m3` | ✅ | BGE-M3 模型路径（本地目录或 HuggingFace 模型名）|
| **SessionMemory v2 召回配置** | | | |
| `SESSION_RECALL_DENSE_TOP_K` | `10` | ❌ | 向量召回每个 session 最多返回条数 |
| `SESSION_RECALL_BM25_TOP_K` | `10` | ❌ | BM25 召回每个 session 最多返回条数 |
| `SESSION_RECALL_RRF_K` | `60` | ❌ | RRF 排序常数（越大，排名差异的影响越小）|
| `SESSION_RECALL_RRF_THRESHOLD` | `0.015` | ❌ | RRF 融合得分最低阈值，低于此值的记忆不返回 |
| `SESSION_RECALL_REQUIRE_MULTI` | `false` | ❌ | 是否要求 dense 和 BM25 双路同时命中（默认单路即算）|
| **服务配置** | | | |
| `DB_POOL_MAX_SIZE` | `2` | ❌ | DbContextPool 最大容量（同时缓存几个数据库的连接）|
| `SSE_HEARTBEAT_INTERVAL` | `15` | ❌ | SSE 心跳间隔（秒），防止代理/防火墙断开长连接 |
| **LangSmith 监控** | | | |
| `LANGCHAIN_TRACING_V2` | `true` | ❌ | 启用 LangSmith 全链路追踪 |
| `LANGCHAIN_API_KEY` | — | ❌ | LangSmith API 密钥 |
| `LANGCHAIN_PROJECT` | `NL2SQL` | ❌ | LangSmith 项目名称 |

### 14.2 配置建议

- 首次部署只需要 `QWEN_API_KEY` 和 `BGE_M3_MODEL_PATH` 即可运行
- LangSmith 配置可选，不配则没有 trace 但不影响功能
- SessionMemory v2 的参数一般不需要调；如果召回太多噪声历史可以降低 `RRF_THRESHOLD` 或提高 `REQUIRE_MULTI=true`
- `DB_POOL_MAX_SIZE` 取决于服务器内存和并发查询量，2 是保守值


---

## 附录：核心数据结构速查

### NL2SQLState（主图共享状态）

```python
class NL2SQLState(TypedDict, total=False):
    # ── 用户输入 ──
    user_query: str
    user_id: str
    database_filter: Optional[str]    # db_id
    query_id: str                     # uuid4().hex[:12]

    # ── IR 产出 ──
    keywords: List[str]
    retrieved_context: Any            # RetrievedContext

    # ── Clarification（Phase 2）──
    clarification_count: int
    clarification_history: List[Dict[str, Any]]
    clarified_keywords: List[str]
    clarification_done: bool

    # ── SS 产出 ──
    selected_schema: List[Any]        # List[MSchemaTable]

    # ── CG 产出 ──
    sql_candidates: List[Any]         # List[SQLCandidate]

    # ── Execution 产出 ──
    schema_text: str                  # M-Schema 的 LLM 文本格式

    # ── Decision 产出 ──
    final_decision: Any               # DecisionResult
    final_sql: str
    final_result: Any
    candidate_scores_r1: List[Dict[str, Any]]
    candidate_scores_r2: Optional[List[Dict[str, Any]]]
    selected_candidate_id: Optional[str]
    fix_failed: bool
    fix_rounds_used: int
    decision_path: str                # A/B/C/D/E/F/G/H

    # ── Verification ──
    answerability_result: Optional[Dict[str, Any]]
    result_verification: Optional[Dict[str, Any]]
    rejection_reason: Optional[str]

    # ── 历史缓存 ──
    conversation_history: List[Dict[str, Any]]
    cache_hit: bool
    cached_sql: Optional[str]
    cache_source: Optional[str]
    cache_confidence: float
    metric_definitions: List[Dict[str, Any]]
    historical_sql_refs: List[Dict[str, Any]]

    # ── 内部注入 ──
    _user_memory: Any                 # UserMemory 实例
    _session_memory: Any              # SessionMemory 实例

    # ── 辅助 ──
    error: Optional[str]
    trace_log: List[str]
```

### DecisionResult（最终决策输出）

```python
@dataclass
class DecisionResult:
    selected_sql: str                              # 最终选定的 SQL
    selected_result: Any                           # 执行结果
    execution_time: float                          # 执行耗时（秒）
    decision_reason: str                           # 决策理由（含路径标签）
    voting_summary: Dict[str, Any]                 # 投票摘要
    candidate_scores_r1: List[Dict[str, Any]]      # R1 数据视角评分明细
    candidate_scores_r2: Optional[List[Dict]]      # R2 SQL 视角评分（可能为 None）
    selected_candidate_id: Optional[str]           # 选中的候选 ID
    fix_failed: bool                               # SmartFix 是否全部失败
    fix_rounds_used: int                           # 实际修复轮次（0-3）
    last_error: Optional[str]                      # 最后发生的错误信息
    decision_path: str                             # 决策路径标识 A/B/C/D/E/F/G/H
```

### SessionQueryMemory（可写入召回库的一轮成功查询）

```python
@dataclass
class SessionQueryMemory:
    historical_query: str          # 历史用户查询
    historical_sql: str            # 历史 final SQL
    user_id: str
    session_id: str
    db_id: str
    conversation_id: str           # = session_id（demo 实现）
    turn_id: int
    success: bool = True
    created_at: str = datetime.now().isoformat(...)

    @property
    def memory_id(self) -> str:
        return f"{self.user_id}:{self.session_id}:{self.db_id}:{self.turn_id}"
```

### HistoricalSQLReference（SessionMemory v2 召回结果）

```python
@dataclass
class HistoricalSQLReference:
    historical_query: str          # 历史用户查询
    historical_sql: str            # 历史 final SQL
    rrf_score: float               # RRF 融合得分
    dense_rank: Optional[int]      # 向量召回排名（未命中时为 None）
    bm25_rank: Optional[int]       # BM25 召回排名（未命中时为 None）
    conversation_id: str
    turn_id: int
    user_id: str = ""
    session_id: str = ""
    db_id: str = ""
    source: str = "session_memory"

    def to_turn(self) -> Dict[str, Any]:
        """转成 HistoryCache 兼容的历史轮次格式"""
        ...

    def to_dict(self) -> Dict[str, Any]:
        """序列化为 dict（传给 CG 弱参考时用）"""
        ...
```
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
- 提供反问 Agent 在 IR 失败/低质场景下主动澄清，并通过用户长期记忆持续优化

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
- **决策**: 使用LangSmith进行全流程监控
- **理由**: 已配置API密钥，支持trace链路追踪和性能分析

---

## 关键设计决策（反问 Agent + 用户记忆）

### 决策 8：用户概念双轨制（系统用户 + 业务用户）

**决策**：本期同时支持两种 `user_id` 来源：
- **系统用户**：登录账号或会话 ID（如 `session_xxx`）。
- **业务用户**：用户在 SQL 上下文中的语义身份（如"销售部小王"）。

**理由**：用户明确表示"两个都做"。系统用户保证记忆隔离的硬约束，业务用户允许同一系统账号下根据角色切换记忆视图。

**实现**：`UserMemory` 类构造时接收 `user_id`（必填）+ `role_tag`（可选）。文件名采用 `{user_id}__{role_tag}.json`，未设 `role_tag` 时退化为 `{user_id}.json`。

---

### 决策 9：联网搜索的用途定位为「领域知识补充」

**决策**：Tavily 搜索仅用于补充系统不理解的**领域术语**，不用于直接回答用户的业务问题。搜索结果作为「上下文背景」喂给 `QuestionGenerator`，绝不直接展示给用户。

**理由**：用户明确："联网搜索的用途应该是补充领域知识"。直接展示搜索结果会让 Agent 越权——它的本职是生成 SQL，不是百科问答。

**触发条件**：仅当触发条件 B（语义不匹配）时启用搜索，且需 LLM 先判断关键词是否属于"未知领域术语"（不是常见数据库字段如 name/date）。

---

### 决策 10：反问粒度可粗可细，由 LLM 自适应

**决策**：不固定反问的"细粒度模式"，由 `QuestionGenerator` 根据触发条件、用户历史、缺失信息量决定：
- **粗粒度**：当用户输入歧义大、缺失关键维度时 → "您指的是哪类商品？食品类还是电子产品？"
- **细粒度**：当只是某个具体值映射不确定时 → "您说的'苹果'，是指 product_name='Apple' 还是 brand='Apple Inc.'？"

**理由**：用户明确"两种反问的粒度都可能"。固定粒度会束缚 Agent 的判断。

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

### 决策 12：反问采用同步暂停 + MCP 协议

**决策**：`UserDialog` 子流程在 LangGraph 中实现为「中断节点」：
- 调用 LangGraph 的 `interrupt()` 机制（参考 `langgraph.types.interrupt`）。
- 等待用户回答恢复后继续流程。
- 通过 MCP 协议暴露给前端（Terminal UI / 未来 Web UI）。

**理由**：用户明确"反问是暂停等待用户回答...遵守 MCP 协议那种"。

---

### 决策 13：最多 5 轮反问，拒答则放行

**决策**：
- 单次用户查询的反问上限为 **5 次**（计数器 `clarification_count`）。
- 用户输入「不知道 / 跳过 / 算了 / skip」等关键词识别为「拒答」，触发后立即退出反问流程，继续走原 IR 结果。
- 5 次上限触发后强制退出，并在 `MemoryWriter` 中记录"未澄清成功"事件。

**理由**：用户明确"连问 5 次。用户拒答就继续按原来的流程走就行"。避免对话陷入无限循环。

---

### 决策 14：会话内搜索结果缓存

**决策**：Tavily 搜索结果以 `query_keyword` 为 Key 缓存在 `ClarificationAgent` 实例的内存字典中，本次 LangGraph 运行内复用，不跨会话持久化。

**理由**：用户明确"复用上次搜索结果"。避免单次会话内对同一术语的重复调用（同一查询可能触发多轮澄清）。

---

### 决策 15：搜索服务商选用 Tavily

**决策**：使用 `tavily-python` SDK，配置项：
- `search_depth="basic"`（足够获取术语定义）
- `max_results=3`
- 超时 10s，失败则跳过 WebSearchEnricher 步骤（不阻塞反问主流程）

**理由**：用户明确"Tavily"。Tavily 免费层 1000 次/月，返回结构化结果（标题+摘要），适合作为 LLM 的上下文。

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

### 决策 18：表/列 schema 检索采用「纯语义相似性，每个 keyword 取 top K」

**决策**：放弃此前讨论过的 BM25 / Hybrid RRF 方案。schema 检索逻辑简化为：

- 只检索**列**（不单建表级 collection）。表名作为列文档的附带说明，自然出现在 metadata 中。
- 对每个 keyword 单独在「列级 collection」做语义向量相似度查询，top K=5。
- 所有 keyword 的结果合并去重（同 `table.column` 保留最高 score），按 score 排序返回。
- 不引入 BM25 / RRF / tokenize 等额外组件。

**理由**：
- 用户明确"表列检索就只采用关键词和列描述之间的语义相似性排序"。
- BIRD schema 描述都是英文且较短，单一向量召回的精度对当前规模足够。
- 简化架构，减少依赖（无需 rank_bm25 / jieba），降低维护成本。
- 表的召回通过"列 → 所属表"反推（`enhance_with_schema`），保证表覆盖率。

**参数**：
- `column_top_k_per_keyword = 5`
- 检索 query：纯 keyword（不拼接原 query 整句），与用户口径一致。

---

### 决策 19：列级文档结构 — 全局单 Collection + 字段拼接 + 列名 boost

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
| `document` | embed 用文本（见下方拼装规则）                                                 |
| `embedding`| BGE-M3 dense 向量（1024 维）                                                  |
| `metadata` | 完整列上下文：database、表名、原列名、人类可读名、数据类型、描述、值描述、格式、PK/FK 标记、引用关系、样本值字符串 |

**document 拼装规则**（按顺序拼接，空字段跳过，连续空格压缩）：

```
{table_name} {original_column_name} {column_name} {data_type}
{column_description} {value_description} {data_format}
{column_name}    ← 末尾再重复一次：boost 列名权重
```

**`column_name` boost 说明**：
- BGE-M3 等 dense embedding 模型本质是 token 语义的加权聚合，重复词项会增加该词对最终向量的影响。
- 重复 `column_name` 一次相当于轻微"加权"，使得用户 query 直接提及列名（如 "AvgScrMath"）时召回更稳。
- 开销可忽略（仅多 1-3 个 token），最坏退化为中性。

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

### 决策 21：检索 query 构造采用「每 keyword 独立检索」

**决策**：列检索时，对 `keywords` 列表中每个 keyword 单独发起一次 `vector_store.query()`，不拼接原始 query 整句。

**伪代码**：

```python
results_pool = []
for kw in keywords:
    kw_emb = embed(kw)
    hits = chroma.query(kw_emb, n_results=5)
    results_pool.extend(hits)

# 去重：同一 column 多次命中取最高分
dedup_by_max_score(results_pool)
```

**理由**：
- 用户明确"每个关键词取前 K 个"。
- 关键词是 LLM 已提炼出的有效信号，单独检索可避免长 query 中无关词稀释语义。
- 不同 keyword 对应不同列时，分别检索召回率高于合并检索。

**Trade-off**：丢失了 keyword 之间的语境（如否定、量级修饰）。若后续验证发现问题，可叠加一次"原 query 整句"检索作为兜底。

---

### 决策 22：全程使用 LangGraph 编排（主图 + 各 Agent 子图）

**决策**：本服务全程基于 LangGraph 进行流程编排，分为两层：

1. **主图（Top-Level Graph）**：编排端到端 NL2SQL 流水线，节点对应各 Agent：
   `IR → Clarification → SS → CG → Execution → Decision → End`
2. **子图（Sub-Graph）**：每个 Agent 内部的多步功能也用 LangGraph `StateGraph` 串联，避免在 Agent 类里写隐式的命令式控制流。

**主图节点示意**：

```
                ┌─────┐
                │START│
                └──┬──┘
                   ▼
              ┌─────────┐
              │ ir_node │  ← InformationRetrievalAgent.run()
              └────┬────┘
                   ▼
            ┌──────────────┐
            │clarification │  ← ClarificationAgent.run()（子图）
            │   _node      │     条件边：clarification_done → ss_node
            └────┬─────────┘                  否则循环回 clarification
                 ▼
              ┌─────────┐
              │ ss_node │  ← SchemaSelectorAgent.run()（子图）
              └────┬────┘
                   ▼
              ┌─────────┐
              │ cg_node │  ← CandidateGeneratorAgent.run()（子图）
              └────┬────┘
                   ▼
              ┌──────────────┐
              │execution_node│  ← ExecutionAgent.run()
              └────┬─────────┘     内含错误修正循环子图
                   ▼
              ┌─────────────┐
              │decision_node│  ← SelfConsistencyDecisionAgent.run()
              └────┬────────┘
                   ▼
                ┌─────┐
                │ END │
                └─────┘
```

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
  - **条件分支与循环**：原生支持（如 Clarification 反问循环、Execution 修正循环、Decision 全部失败兜底）。
  - **interrupt/resume**：Clarification 子图的"暂停等用户回答"机制原生支持，无需自己实现。
  - **可插拔**：节点可按配置启用/禁用（如 Clarification 可关闭）。

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

## 触发条件详细规约

| 条件 ID | 名称 | 检测逻辑 | 是否触发搜索 |
|---------|------|----------|--------------|
| A | 召回为空 | LSH + 向量检索后所有候选数为 0 | 否 |
| B | 语义不匹配 | 召回的 Top-1 值与查询关键词在 LLM 判断下"语义不一致"（如"苹果"召回"Apple Inc."但上下文是食品） | **是** |
| C | 低相似度 | Top-1 相似度 < 0.4（向量）或 < 0.3（LSH Jaccard） | 否 |
| D | 用户记忆冲突 | 召回值与用户历史 `term_preferences` 中的映射冲突 | 否 |

**重要约束**：用户明确"反问的情况只在 2 的情况下触发"——这里指的是 B 类（语义不匹配）必然触发反问，而 A/C/D 在配置允许时也会触发但不调用搜索。

> 待澄清项：是否所有 4 类都默认开启反问？设计采用「全部开启 + 配置可关」策略，配置文件中提供 `clarification.triggers.{A,B,C,D}: bool`。

---

## 模块结构（反问相关）

```
src/
├── clarification/
│   ├── __init__.py
│   ├── agent.py                # ClarificationAgent 主类（LangGraph 子图）
│   ├── trigger.py              # TriggerDetector：判断是否反问
│   ├── web_search.py           # WebSearchEnricher：Tavily 调用 + 会话缓存
│   ├── question_generator.py   # QuestionGenerator：LLM 生成反问
│   └── dialog.py               # UserDialog：interrupt + 循环
└── memory/
    ├── __init__.py
    ├── user_memory.py          # UserMemory 主类
    └── storage.py              # JSON 文件读写 + 文件锁
```

---

## LangGraph 集成示意

```python
from langgraph.graph import StateGraph
from langgraph.types import interrupt

graph = StateGraph(NL2SQLState)
graph.add_node("ir", retrieval_node)
graph.add_node("clarification", clarification_node)  # 新增
graph.add_node("ss", schema_selection_node)
graph.add_node("cg", candidate_generation_node)

graph.add_edge("ir", "clarification")
graph.add_conditional_edges(
    "clarification",
    lambda s: "ss" if s["clarification_done"] else "clarification",  # 反问循环
)
graph.add_edge("ss", "cg")
```

`NL2SQLState` 需新增字段：
```python
class NL2SQLState(TypedDict):
    # ... existing fields ...
    clarification_count: int            # 已反问次数
    clarification_history: List[Dict]   # 本次查询的反问历史
    clarified_keywords: Dict[str, str]  # 澄清后的关键词映射
    web_search_cache: Dict[str, Any]    # 会话内搜索缓存
    user_id: str                        # 用户标识
```

---

## 测试策略（反问相关）

| 测试类型 | 覆盖场景 |
|----------|----------|
| 单元测试 | `TriggerDetector` 四类触发判断；`UserMemory` CRUD；JSON 文件锁并发 |
| 集成测试 | 模拟 IR 结果 → ClarificationAgent → 反问/放行决策正确 |
| Mock 测试 | Tavily 失败、LLM 超时、用户拒答等异常路径 |
| 端到端 | LangGraph 完整运行一次含反问的查询，验证 MemoryWriter 正确写入 |

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

**反问 Agent 相关风险与缓解**:

| 风险 | 缓解方案 |
|------|----------|
| Tavily API 配额耗尽 | 会话缓存 + 失败降级（跳过搜索直接生成反问） |
| 用户被反问烦扰 | 5 次硬上限 + 拒答关键词识别 |
| 记忆文件损坏 | 写入采用「先写临时文件 + 原子 rename」+ 备份上次版本 |
| LLM 误判触发条件 | TriggerDetector 提供配置开关，可逐项关闭 |
| 反问粒度不自然 | QuestionGenerator 的 Prompt 中提供粗/细粒度示例，并要求 LLM 解释选择理由（日志记录） |

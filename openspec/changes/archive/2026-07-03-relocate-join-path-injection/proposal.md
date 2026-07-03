# Relocate JOIN Path Injection to SQL Generation Stage

## Why

当前表关联（JOIN）路径的注入发生在 **IR 阶段**：`InformationRetrieval.retrieve()` 调用
`_inject_join_paths()`，依据 IR 召回的表集合（通常 10~20 张）计算 JOIN 路径，把结果写入
`RetrievedContext.join_paths` / `join_paths_text`，并经 `_add_bridge_tables()` 从 ChromaDB
补充桥接表的 M-Schema。

这套实现存在两个核心问题：

1. **死字段——注入后零消费**。全仓库检索 `join_paths` / `join_paths_text`，二者仅在
   `information_retrieval.py` 中被定义与赋值，SS / CG / Execution / Decision 任何下游节点
   均未读取。`join_paths_text` 字段注释自称「用于 Prompt 注入」，但该注入步骤从未实现。
   即 JOIN 路径算了、写了，却从未进入任何 LLM Prompt。

2. **时机错误——在 schema 收窄之前算关系**。JOIN 路径基于 IR 召回的全量表集合计算，但下游
   SS 会把表集合裁剪到 2~4 张。IR 算出的关系绝大多数针对最终不会使用的表，既浪费计算，
   也无法保证最终选中的表之间的 JOIN 关系被正确呈现（IR 算的是「召回表之间」而非
   「选中表之间」的最短路径，二者可能不一致）。

本变更把 JOIN 路径注入从 IR 阶段**后移到 SQL 生成环节**——确切地说，移到 `single_query_graph`
流水线中 **SS 之后、CG 之前**的新增 `schema_finalize` 节点。此时 `selected_schema` 已收窄到
真正参与查询的表，JOIN 路径与桥接表补全都基于这份精简集合计算，结果直接进入 CG 的生成 Prompt
与 SmartFix 的修复 Prompt。

## What Changes

```
single_query_graph 节点链变更：
  旧: ir → ss → [answerability_check] → cg → execution → decision
  新: ir → ss → schema_finalize → [answerability_check] → cg → execution → decision
                     │
                     ├─ 读 selected_schema 表名集合
                     ├─ extract_join_paths(graph, tables) → 边 + 桥接表
                     ├─ 桥接表 M-Schema 补进 selected_schema
                     └─ join_paths_text 写入 NL2SQLState
```

1. **新增 `schema_finalize` 节点**（插在 `ss` 与 `answerability_check`/`cg` 之间）：
   基于收窄后的 `selected_schema` 计算表间 JOIN 路径，补充桥接表 M-Schema，产出
   `join_paths_text` 写回 `NL2SQLState`。
2. **桥接表补全放在 `answerability_check` 之前**：可回答性判断「这些表能否连起来」需要看到
   桥接表存在，否则可能把「需要桥接表才能 JOIN」误判为不可回答。
3. **JOIN 逻辑下沉为纯函数** `schema_graph_builder.enrich_schema_with_join_paths()`：
   操作 `List[MSchemaTable]`（而非 `RetrievedContext`），运行时依赖（`vector_store` /
   `vectorizer` / `data_dir`）通过参数注入，保持 `schema_graph_builder` 模块的纯工具定位。
4. **`join_paths_text` 双消费**：CG 子图生成 Prompt 时追加为 `HumanMessage`（沿用现有
   preference / metric / historical 的追加模式）；Execution 节点生成 `schema_text` 时拼接
   `join_paths_text`，使其流向 Decision 的 SmartFix 修复 Prompt。
5. **删除 IR 死代码**：`RetrievedContext.join_paths` / `join_paths_text` 字段、
   `InformationRetrieval._inject_join_paths()` / `_add_bridge_tables()` 方法、`retrieve()`
   中对二者的调用。
6. **`NL2SQLState` 新增 `join_paths_text: str` 字段**，作为 `schema_finalize` → CG / Execution
   的传递通道。
7. **cache_hit 短路不参与**：`single_query_graph` 入口 `cache_hit → execution` 短路跳过整个
   SS/schema_finalize 链；cached SQL 自带正确 JOIN，无需重新注入。

## Capabilities

### Modified Capabilities
- `single-query-pipeline`: 流水线节点链插入 `schema_finalize` 节点（SS 之后、answerability_check
  之前）；新增 `make_schema_finalize_node` 节点工厂复用约定；`schema_finalize` 的 fail-fast 行为。

### New Capabilities
- `schema-relationship-graph`: JOIN 路径计算与注入的语义契约——`enrich_schema_with_join_paths()`
  纯函数接口（输入 `selected_schema` + 运行时依赖，输出补全后的 schema + `join_paths_text`）、
  桥接表 M-Schema 补全规则、`join_paths_text` 的双消费契约（CG 生成 Prompt + SmartFix 修复 Prompt）。
  > 该 capability 在 `nl2sql-agent-system` 的 proposal 中已声明但未独立成 spec 文件（其 JOIN 注入
  > Scenario 当时写在 `information-retrieval.md` 内）。本变更新建独立 spec，并将 JOIN 注入语义
  > 从 IR 阶段迁移到 SS→CG 之间。

### Coordination（跨 change 协调）
本变更覆盖 `nl2sql-agent-system` 的**决策 26（JOIN 路径注入）**。实施时需同步：
- 从 `nl2sql-agent-system/specs/information-retrieval.md` 移除「JOIN 路径注入在 IR」相关 Scenario；
- 在 `nl2sql-agent-system/specs/sql-generation.md` 补充「CG Prompt 追加 join_paths_text」Scenario
  （或留待本 capability spec 覆盖）。
- 归档顺序：若 `nl2sql-agent-system` 先归档，须先按上述清理其 information-retrieval spec，避免
  两份冲突的 JOIN 注入契约同时进入主 specs。

## Impact

**受影响的代码**：
- `src/graph/single_query_graph.py`：新增 `schema_finalize` 节点 + 调整 `ss` 出口边。
- `src/graph/main_graph.py`：新增 `make_schema_finalize_node()` 节点工厂（沿用 `_wrap_node` 风格）。
- `src/preprocessing/schema_graph_builder.py`：新增 `enrich_schema_with_join_paths()` 纯函数
  （由 `_inject_join_paths` / `_add_bridge_tables` 逻辑重构而来，操作对象改为 `MSchemaTable`）。
- `src/retrieval/information_retrieval.py`：删除 `_inject_join_paths()` / `_add_bridge_tables()`
  方法、`retrieve()` 中的调用、`RetrievedContext.join_paths` / `join_paths_text` 字段。
- `src/graph/state.py`：`NL2SQLState` 新增 `join_paths_text: str` 字段及初始值。
- `src/sql_generation/cg_graph.py`：`node_llm_generate_and_validate` 追加 `join_paths_text`
  HumanMessage。
- `src/graph/main_graph.py`（Execution 节点）：生成 `schema_text` 时拼接 `join_paths_text`。
- `tests/preprocessing/test_schema_graph_builder.py`：`test_add_bridge_tables` 迁移到新函数；
  新增 `enrich_schema_with_join_paths` 单测。
- `tests/graph/test_subgraphs.py`（或等价）：新增 `schema_finalize` 节点在流水线中的编排断言。

**非功能影响**：
- **延迟**：JOIN 路径计算从「基于 IR 全量表集合」改为「基于 SS 收窄后的 2~4 张表」，BFS 计算
  量与桥接表 ChromaDB 查询量显著下降；`schema_finalize` 约增加 50~150ms（图加载 + BFS + 必要时
  桥接表查询），但换来 CG / SmartFix Prompt 实际获得 JOIN 信息，SQL 正确率提升。
- **正确性**：修复「JOIN 路径算了但从未注入 Prompt」的缺陷，多表 JOIN 类查询的候选 SQL 与修复
  SQL 均能获得表关联提示。
- **token**：`join_paths_text` 进入 Prompt 增加少量 token（仅多表查询时非空）。

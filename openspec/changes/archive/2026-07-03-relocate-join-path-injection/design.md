# Design: Relocate JOIN Path Injection to SQL Generation Stage

## 1. 背景与问题

### 1.1 当前实现（IR 阶段注入，死字段）

`InformationRetrieval.retrieve()` 第 7 步调用 `_inject_join_paths(context, database_filter)`：

- 从 `RetrievedContext.get_all_table_names()` 取 IR 召回的**全量表集合**（通常 10~20 张）；
- 加载 `data/preprocessed/schema_graphs/{db_id}.json`，调 `extract_join_paths(graph, tables)` 做 BFS
  最短路径提取，得到 `edges` + `bridge_tables`；
- `edges` → `context.join_paths`，`format_join_paths_for_prompt()` → `context.join_paths_text`；
- `bridge_tables` → `_add_bridge_tables()`：用 `self.vector_store` + `self._vectorizer` 从 ChromaDB
  查桥接表的列，追加为 `RetrievedItem` 进 `context.tables` / `context.columns`。

**问题 A（死字段）**：全仓库 `join_paths` / `join_paths_text` 仅在 `information_retrieval.py` 中
定义与赋值。SS 的 `to_mschema()` 只读 `retrieved_context.tables` / `.columns`；CG 的 Prompt 追加项
是 preference / metric / historical，不含 join；Execution 生成 `schema_text` 只用 `selected_schema`；
Decision/SmartFix 走 `schema_text` 通道。**下游零消费**。

**问题 B（时机错）**：JOIN 路径基于 IR 召回的全量表集合计算，但 SS 会把表裁剪到 2~4 张。IR 算出的
关系多数针对最终不用的表；且「召回表之间的最短路径」≠「选中表之间的最短路径」，桥接表判定也随之失准。

### 1.2 最新图结构（refactor-single-query-graph 之后）

单查询流水线已下沉到 `single_query_graph`（`src/graph/single_query_graph.py`），主图只管分流 + 记忆：

```
single_query_graph:
  START ─┬─cache_hit→ execution → decision → END
         └─→ ir → ss ─┬─有schema→ [answerability_check] → cg → execution → decision → END
                      └─无schema→ END
```

节点复用 `main_graph` 的 6 个 `make_*_node` 工厂。本变更在该链中插入 `schema_finalize`，只需动
`single_query_graph.py` + 在 `main_graph.py` 加一个工厂函数，主图编排无需改动。

## 2. 方案：新增 `schema_finalize` 节点

### 2.1 节点链

```
ir → ss → schema_finalize → [answerability_check] → cg → execution → decision
            │
            ├─ 读 selected_schema 的表名集合
            ├─ enrich_schema_with_join_paths(...)  ← 下沉的纯函数
            │     ├─ load graph + extract_join_paths → edges + bridge_tables
            │     ├─ 桥接表 → ChromaDB 查列 → 转 MSchemaTable 补进 selected_schema
            │     └─ format_join_paths_for_prompt → join_paths_text
            ├─ 回写 selected_schema（含桥接表）到 state
            └─ 回写 join_paths_text 到 state
```

`ss` 的出口条件边改为 `→ schema_finalize`；`schema_finalize → answerability_check`（启用时）或
`→ cg`。`schema_finalize` 本身无 fail-fast 早退需求（无 JOIN 路径时 `join_paths_text=""`，schema 不变，
正常放行）。

### 2.2 关键决策 1：桥接表补全放在 answerability_check 之前

桥接表场景：用户问「订单金额和客户姓名」，SS 选中 `{orders, customers}`，二者可能无直接 FK，
真实路径 `orders → order_customer_map → customers`，`order_customer_map` 是桥接表。

可回答性检查（决策 23）需判断「这些表能否连起来」。若桥接表 schema 在 answerability_check **之后**
才补，则可回答性判断时看不到桥接表，可能把「需要桥接表才能 JOIN」误判为不可回答而拦截。故
`schema_finalize` 必须在 `answerability_check` **之前**完成桥接表补全。

### 2.3 关键决策 2：JOIN 逻辑下沉为纯函数

把 `_inject_join_paths` / `_add_bridge_tables` 重构为
`schema_graph_builder.enrich_schema_with_join_paths()` 纯函数：

```python
def enrich_schema_with_join_paths(
    selected_schema: List[MSchemaTable],   # SS 产物（收窄后的表）
    database_filter: str,                  # 定位 schema_graphs/{db}.json
    vector_store,                          # 运行时依赖，参数注入（查桥接表列）
    vectorizer,                            # 运行时依赖，参数注入（embed 表名做 query）
    data_dir: str,                         # 定位 schema_graphs 目录
) -> tuple[List[MSchemaTable], str]:
    """
    返回 (补全桥接表后的 selected_schema, join_paths_text)。
    - 操作对象是 MSchemaTable（SS 产物），而非 RetrievedContext（IR 产物）。
    - 运行时依赖靠参数注入，schema_graph_builder 不硬实例化 ChromaDB / 向量器。
    - database_filter 为空 / 表数 < 2 / 图不存在 / 无 edges 时：
      selected_schema 原样返回，join_paths_text=""。
    """
```

**为何操作 `MSchemaTable` 而非 `RetrievedContext`**：新场景的输入是 SS 的 `selected_schema`
（`List[MSchemaTable]`，列是 `MSchemaColumn`），与 IR 的 `RetrievedContext`（列是 `RetrievedItem`）
是两套数据结构。沿用 `RetrievedContext` 会迫使节点把 `selected_schema` 反向转回
`RetrievedContext` 再转出，徒增转换层。直接面向 `MSchemaTable` 更直接。

**桥接表列的元数据来源**：ChromaDB `nl2sql_columns` collection 的 metadata 字段含 `data_type` /
`description` / `sample_values` / `is_primary_key` / `references` / `original_column_name` /
`table_name`，与 SS `to_mschema()` 从 `col_item.metadata` 取的字段同源同构，可直接复用同款转换逻辑
构造 `MSchemaColumn` / `MSchemaTable`。

### 2.4 关键决策 3：join_paths_text 双消费

`join_paths_text` 存入 `NL2SQLState`，供两处消费：

```
NL2SQLState.join_paths_text (schema_finalize 写入)
        │
        ├─① CG 子图 node_llm_generate_and_validate
        │     追加 HumanMessage("## 表关联\n{join_paths_text}")
        │     （沿用 cg_graph.py:108-125 的 preference/metric/historical 追加模式）
        │
        └─② Execution 节点生成 schema_text
              schema_text = format_for_llm(selected_schema)
                          + ("\n\n## 表关联\n" + join_paths_text if join_paths_text else "")
              → 流向 Decision 子图 SmartFix 修复 Prompt（decision_graph 走 schema_text 通道）
```

**为何 Execution 才拼**：`schema_text` 当前在 Execution 节点（CG 之后）生成
（`main_graph.py` make_execution_node）。SmartFix 在 Decision 子图用 `schema_text`。故 JOIN 文本
需在 `schema_text` 生成处拼接，确保修复 SQL 时也能看到表关联。

### 2.5 关键决策 4：cache_hit 短路不参与

`single_query_graph` 入口 `cache_hit → execution` 短路跳过 ir/ss/schema_finalize/cg。cached SQL 是
历史命中 SQL，生成时已含正确 JOIN，直接执行即可，无需重新注入。`schema_finalize` 不在短路路径上，
行为正确。

### 2.6 删除项

- `RetrievedContext.join_paths` / `join_paths_text` 字段及其 `__post_init__` 初始化。
- `InformationRetrieval._inject_join_paths()` / `_add_bridge_tables()` 方法。
- `retrieve()` 第 7 步 `context = self._inject_join_paths(...)` 调用。
- `tests/preprocessing/test_schema_graph_builder.py::test_add_bridge_tables`（迁移到新函数单测）。

`extract_join_paths()` / `format_join_paths_for_prompt()` 保留不动（稳定工具函数，被新纯函数复用）。

## 3. 数据流（变更后）

```
                 NL2SQLState
                     │
   ir ──► retrieved_context ──► ss
                                  │
                                  ▼
                          selected_schema (2~4 表)
                                  │
                                  ▼
                        schema_finalize ──► enrich_schema_with_join_paths
                                  │           ├─ selected_schema' (含桥接表)
                                  │           └─ join_paths_text
                                  │
                ┌─────────────────┴──────────────────┐
                ▼                                    ▼
        answerability_check                       (join_paths_text 存 state)
                │                                    │
                ▼                                    │
              cg ◄──────────────────────────────────┤ 追加 HumanMessage
                │                                    │
                ▼                                    │
          execution ◄────────────────────────────────┤ schema_text += join_paths_text
                │                                    │
                ▼                                    │
          decision (SmartFix 用 schema_text) ◄───────┘
```

## 4. 与 nl2sql-agent-system 的协调

本变更覆盖 `nl2sql-agent-system` 决策 26（JOIN 路径注入）。`nl2sql-agent-system` 仍在进行中
（171 任务完成 138），其 `specs/information-retrieval.md` 中的 JOIN 注入 Scenario 尚未归档到主 specs。

实施与归档约束：
- **实施时**：同步从 `nl2sql-agent-system/specs/information-retrieval.md` 移除「JOIN 路径注入在 IR」
  Scenario；在 `nl2sql-agent-system/specs/sql-generation.md` 补充 CG Prompt 注入说明（或由本变更
  `schema-relationship-graph` spec 覆盖）。
- **归档时**：若 `nl2sql-agent-system` 先归档，须先完成上述 spec 清理，避免「JOIN 注入在 IR」与
  「JOIN 注入在 SS→CG」两份冲突契约同时进入主 specs。本变更的 `schema-relationship-graph` spec
  作为 JOIN 注入语义的最终事实来源。

## 5. 风险与回退

- **风险**：`schema_finalize` 引入新节点，若 `enrich_schema_with_join_paths` 抛异常需兜底——
  节点内 try/except，异常时 `join_paths_text=""`、`selected_schema` 原样放行（降级为无 JOIN 提示），
  不阻断流水线。
- **回退**：若新节点引发回归，可临时把 `single_query_graph` 的 `ss` 出口边直接接
  `answerability_check`/`cg`（跳过 schema_finalize），`join_paths_text` 恒为空，行为退回当前
  （无 JOIN 注入），不影响正确性底线（当前本就无 JOIN 注入）。

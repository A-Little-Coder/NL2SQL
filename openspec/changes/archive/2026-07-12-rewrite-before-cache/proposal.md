## Why

当前流水线缺乏独立的 NL 问句改写环节，指代消解、歧义处理、对象缺失补全等能力分散在 IR 关键词提取的 prompt 中和 TaskPlanner 的隐式逻辑中，导致：

1. 指代消解依赖 LLM 在关键词提取时"顺便"处理，不可控、不可观测
2. TaskPlanner 同时承担意图理解、歧义反问、拒答判定三个职责，职责过重，且到 TaskPlanner 的 query 可能仍含指代/歧义
3. 改写后的完整语义 query 无法用于 cache 命中检测，降低了缓存命中率
4. 拒答轮次不写入会话历史，用户无法在下一轮基于上下文继续提问

## What Changes

### 新增

- **前置拒答检测节点**（PreReject）：在 START 之后第一个节点，硬性检测写操作/空查询等违规，不调 LLM，违规直接拒答 END
- **Rewrite 子图**（`src/rewrite/`）：在 HistoryCache 之前，包含两个子节点（问题检测 + 改写执行）通过条件边循环协作：
  - 检测指代/歧义/对象缺失 → 利用前 5 轮上下文改写 → 再检测 → 最多 2 次改写循环
  - 2 次改写后仍有问题 → 触发反问澄清（interrupt），用户补充信息后继续改写
  - 反问可多次，直到检测通过
- **改写拒答写会话**：Rewrite 拒答的轮次也写入会话历史，用户可在下一轮解释清楚后继续

### 修改

- **history_cache 位置后移**：从 START 后第一个节点改为 Rewrite 子图之后，改写后的完整 query 去命中缓存
- **TaskPlanner → TaskDecomposer**（`src/clarification/task_decomposer.py`）：改名，删除所有拒答/反问功能，只保留意图拆解（单意图/多意图）
- **IR 删除隐式消歧**：删除 `KEYWORD_EXTRACTION_WITH_HISTORY_PROMPT`，不再传 `conversation_history` 给关键词提取

### 删除

- TaskPlanner 的 REJECT 裁决逻辑 + CLARIFY 反问逻辑（prompt 规则 + 代码分支）
- TaskPlanner 的 `_detect_write_operation` 写操作检测（移至前置拒答检测）
- IR 的 `KEYWORD_EXTRACTION_WITH_HISTORY_PROMPT` 和历史参数传递

## Capabilities

### New Capabilities
- `query-rewrite`: 在 NL 问句进入流水线之前，检测指代/歧义/对象缺失，利用会话历史改写为完整语义 query，支持最多 2 次改写循环 + 反问澄清循环，改写失败时拒答

### Modified Capabilities
- `single-query-pipeline`: 主图入口从 history_cache 改为 pre_reject → rewrite 子图，新增多层条件边路由
- `keyword-extraction-history-isolation`: 删除该能力——关键词提取不再依赖会话历史做隐式消歧，全部交由 Rewrite 环节处理
- `memory-storage`: `_should_write_session_turn` 放宽条件，Rewrite 拒答轮次也写入会话历史

## Impact

- **新增模块**: `src/rewrite/`（rewrite_graph.py, prompts.py, `__init__.py`）
- **修改文件**:
  - `src/graph/main_graph.py` — 图结构重构（pre_reject + rewrite 子图 + 路由变更）
  - `src/graph/state.py` — 新增字段
  - `src/clarification/task_planner.py` — 重命名为 task_decomposer.py + 删除拒答/反问逻辑
  - `src/clarification/prompts.py` — 删除 REJECT/CLARIFY 规则
  - `src/retrieval/prompts.py` — 删除 WITH_HISTORY 版本
  - `src/retrieval/ir_graph.py` — 删除 conversation_history 传递
  - `src/retrieval/information_retrieval.py` — 删除 history 参数
  - `src/api/routes/query.py` — _should_write_session_turn 放宽
- **测试**: 新增 Rewrite 模块测试，更新 TaskDecomposer 测试
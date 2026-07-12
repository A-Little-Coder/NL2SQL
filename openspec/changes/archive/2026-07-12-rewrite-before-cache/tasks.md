## 1. 新建前置拒答检测节点（PreReject）

- [x] 1.1 创建 `src/rewrite/pre_reject.py`，实现 `make_pre_reject_node()` 工厂函数，包含硬性写操作检测（`_detect_write_operation`）和空查询检测，检测到违规时设 `rejection_reason` + `rewrite_rejection_reason`
- [x] 1.2 更新 `src/rewrite/__init__.py` 导出 `make_pre_reject_node`

## 2. 新建 Rewrite 子图

- [x] 2.1 创建 `src/rewrite/rewrite_subgraph.py`，定义 `RewriteSubgraphState`（含 `user_query`/`rewritten_query`/`rewrite_round`/`rewrite_reason`/`conversation_history`/`clarify_context` 等字段）
- [x] 2.2 实现 `make_detect_issues_node()`：调用 LLM 检测指代/歧义/对象缺失，输出 verdict（pass/has_issues）
- [x] 2.3 实现 `make_rewrite_execute_node()`：调用 LLM 利用会话历史改写 query，emit `rewrite` SSE 事件
- [x] 2.4 实现 `make_clarify_node()`：interrupt 挂起，等待用户补充信息，将补充信息放入 `clarify_context`
- [x] 2.5 组装 Rewrite 子图：条件边路由（改写循环最多 2 次，不足则触发反问，反问后继续改写循环，直到检测通过）
- [x] 2.6 更新 `src/rewrite/__init__.py` 导出 `build_rewrite_subgraph`
- [x] 2.7 更新 `src/rewrite/prompts.py`：定义 `DETECT_ISSUES_PROMPT`（检测指代/歧义/缺失）和 `REWRITE_EXECUTE_PROMPT`（利用上下文改写）

## 3. 修改 State 定义

- [x] 3.1 `src/graph/state.py`：新增 `rewrite_rejection_reason: Optional[str]`（前置拒答检测用）
- [x] 3.2 `src/graph/state.py`：新增 `rewritten_query: str`、`rewrite_round: int`、`rewrite_reason: str`（Rewrite 子图用）
- [x] 3.3 `src/graph/state.py`：新增 `clarify_context: Optional[str]`（反问澄清用，复用已有 `clarify_round`/`clarify_question`）
- [x] 3.4 `src/graph/state.py`：更新 `create_initial_state()` 设置新字段默认值

## 4. 重构主图结构

- [x] 4.1 `src/graph/main_graph.py`：图入口改为 `START → pre_reject`
- [x] 4.2 `src/graph/main_graph.py`：新增 `pre_reject → [reject] → END` / `pre_reject → [pass] → rewrite` 条件边路由
- [x] 4.3 `src/graph/main_graph.py`：新增 `rewrite` 节点（调用 Rewrite 子图），`rewrite → history_cache`
- [x] 4.4 `src/graph/main_graph.py`：更新 `build_main_graph` 参数，接收 `llm_client` 供 Rewrite 子图使用

## 5. TaskDecomposer 精简

- [x] 5.1 `src/clarification/prompts.py`：删除 CLARIFY 裁决规则，只保留 EXECUTE 意图拆解
- [x] 5.2 `src/clarification/task_decomposer.py`：删除 `_detect_write_operation`、删除 CLARIFY 相关逻辑、删除 `conversation_history` 参数
- [x] 5.3 `src/clarification/__init__.py`：更新导出名
- [x] 5.4 全局搜索替换所有 `TaskPlanner`/`task_planner` 引用为 `TaskDecomposer`/`task_decomposer`

## 6. IR 删除隐式消歧

- [x] 6.1 `src/retrieval/prompts.py`：删除 `KEYWORD_EXTRACTION_WITH_HISTORY_PROMPT`
- [x] 6.2 `src/retrieval/ir_graph.py`：删除 `IRGraphState` 中的 `conversation_history` 字段，删除 `node_extract_keywords` 中的 `conversation_history` 传递
- [x] 6.3 `src/retrieval/information_retrieval.py`：删除 `extract_keywords()` 的 `conversation_history` 参数，始终使用 `KEYWORD_EXTRACTION_PROMPT`

## 7. API 层修改

- [x] 7.1 `src/api/routes/query.py`：修改 `_should_write_session_turn`，Rewrite 拒答（`rewrite_rejection_reason` 非空）时也允许写入会话历史
- [x] 7.2 `src/api/routes/query.py`：turn_data 增加 `rewrite_rejection_reason` 字段
- [x] 7.3 SSE 事件新增 `rewrite` event_type（含 `rewritten_query`、`rewrite_reason`、`rewrite_round` 字段）

## 8. 清理旧代码

- [x] 8.1 删除 v1 的 `src/rewrite/rewrite_graph.py`（旧版 `make_rewrite_node`，已被新的子图架构替代）
- [x] 8.2 删除 `src/clarification/dialog.py`（反问逻辑已移至 Rewrite 子图）

## 9. 更新测试

- [x] 9.1 更新 `tests/graph/test_rewrite.py`：适配新的 PreReject + Rewrite 子图结构
- [x] 9.2 更新 `tests/clarification/test_task_decomposer.py`：删除 CLARIFY 相关测试
- [x] 9.3 更新 `tests/clarification/test_agent_integration.py`：适配图结构变化
- [x] 9.4 删除 `tests/clarification/test_dialog.py`（dialog.py 已删除）
- [x] 9.5 运行全量测试确保通过
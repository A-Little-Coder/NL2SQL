# Implementation Tasks

## 1. LLMClient 中文思考指令注入（独立可回归）

- [x] 1.1 在 `utils/llm_client.py` 中添加 `_CHINESE_THINKING_DEFAULT` 环境变量读取（默认 `true`）
- [x] 1.2 实现 `_inject_chinese_thinking(messages)` 辅助方法：检查首条 system message，存在则追加，不存在则插入新条
- [x] 1.3 在 `chat()` 和 `chat_stream()` 入口调用 `_inject_chinese_thinking`
- [x] 1.4 添加单元测试 `tests/utils/test_llm_client_chinese_thinking.py`：
  - 验证有 system message 时追加
  - 验证无 system message 时插入
  - 验证环境变量 `LLM_CHINESE_THINKING=false` 时不注入
- [ ] 1.5 手动验证：跑一条真实查询，前端 SSE `llm_thinking` 事件确认输出中文

## 2. 数据结构扩展

- [x] 2.1 `src/graph/state.py`：新增 `NL2SQLState` 字段
  - `candidate_scores_r1: List[Dict]`
  - `candidate_scores_r2: Optional[List[Dict]]`
  - `selected_candidate_id: Optional[str]`
  - `fix_failed: bool`
  - `fix_rounds_used: int`
  - `last_error: Optional[str]`
  - `fix_history: List[Dict]`
- [x] 2.2 `src/decision/self_consistency.py`：扩展 `DecisionResult` dataclass
  - 新增 `candidate_scores_r1` / `candidate_scores_r2` / `selected_candidate_id`
  - 新增 `fix_failed` / `fix_rounds_used` / `last_error` / `decision_path`
- [x] 2.3 `src/execution/executor.py`：错误等级映射 `ERROR_SEVERITY`
  ```python
  ERROR_SEVERITY = {
      ErrorType.SEMANTIC_ERROR: 1,
      ErrorType.SYNTAX_ERROR: 2,
      ErrorType.UNKNOWN: 3,
      ErrorType.TIMEOUT_ERROR: 4,
      ErrorType.RUNTIME_ERROR: 5,
      ErrorType.PERMISSION_ERROR: 6,
  }
  UNFIXABLE_ERRORS = {ErrorType.TIMEOUT_ERROR, ErrorType.RUNTIME_ERROR, ErrorType.PERMISSION_ERROR}
  ```

## 3. ExecuteAll 节点（一次性执行无修复）

- [x] 3.1 `src/graph/main_graph.py`：重写 `make_execution_node`，去除内部修复循环
  - 5 候选逐个调用 `executor.execute(cand.sql)`（**不**经过 `SQLFixLoop`）
  - 回填 `cand.result` / `cand.execution_time` / `cand.status` / `cand.error_message`
- [x] 3.2 SSE 业务事件保持不变（`execution` 事件每候选 emit 一次）
- [x] 3.3 单元测试 `tests/graph/test_execute_all_node.py`：
  - 验证 5 候选都执行
  - 验证不触发任何 LLM 修复调用（mock LLMClient，断言 `chat_json.call_count == 0`）

## 4. ScoreByData (R1) 节点

- [x] 4.1 `src/decision/self_consistency.py`：新增 `score_by_data(candidates, user_query) -> List[Dict]`
  - 输入：成功候选列表 + user_query
  - 构建 prompt（数据视角评分标准 + cell 截断 20 + top-20 行 + 明示节选）
  - 隐藏 SQL 代码
  - 调用 `llm_client.chat_json`
  - 返回 `[{id, score: 0-5, reason}]`
- [x] 4.2 SSE 业务事件：发送 `score_r1` 事件（含全部候选评分）
- [x] 4.3 单元测试 `tests/decision/test_score_by_data.py`：
  - 验证 prompt 不包含 SQL 代码
  - 验证 cell 截断到 20 字符
  - 验证 top-20 行约束
  - 验证 prompt 包含"结果为 top-20 节选"提示
  - 验证返回格式校验

## 5. ScoreBySQL (R2) 节点

- [x] 5.1 `src/decision/self_consistency.py`：新增 `score_by_sql(candidates, user_query, r1_scores) -> List[Dict]`
  - 输入：候选列表 + user_query + R1 评分
  - 构建 prompt（严格模式评分标准 + SQL + 执行时间 + R1 评价）
  - 调用 `llm_client.chat_json`
  - 返回 `[{id, score: 0-5, reason}]`
- [x] 5.2 SSE 业务事件：发送 `score_r2` 事件（含触发原因和评分）
- [x] 5.3 单元测试 `tests/decision/test_score_by_sql.py`：
  - 验证 prompt 包含 SQL 代码 + R1 评价
  - 验证返回格式校验

## 6. 决策路由逻辑

- [x] 6.1 `src/decision/self_consistency.py`：新增 `_pick_from_scores(candidates, scores) -> (best_id, is_tied, top_score)`
  - 找出最高分组
  - 返回（最高分候选 ID，是否并列，最高分值）
  - 并列时按 `execution_time` 选最快
- [x] 6.2 单元测试 `tests/decision/test_pick_from_scores.py`：
  - 唯一最高 → 返回该 ID, is_tied=False
  - 并列最高 → 返回最快 ID, is_tied=True
  - 全部 0 分 → 返回第一个（边界）

## 7. SmartFix（单候选 ≤3 轮）

- [x] 7.1 `src/execution/executor.py`：重构 `SQLFixLoop`
  - `max_retries` 默认改为 3
  - `run()` 内部维护 `fix_history: List[Dict]`
  - 每轮失败后 `fix_history.append({round, sql, error})`
  - 修复 prompt 携带 `fix_history`
- [x] 7.2 修改 `SQL_FIX_PROMPT` 模板，加入 `{fix_history}` 占位符
  ```
  历次修复尝试：
  {fix_history_formatted}
  请基于这些历史，避免再犯同样的错。
  ```
- [x] 7.3 `_try_fix()` 入口加错误类型过滤：若 `error.error_type in UNFIXABLE_ERRORS` 直接返回 `None`
- [x] 7.4 SSE 业务事件：每轮发送 `smart_fix_round` 事件（含 round / sql / error / success）
- [x] 7.5 单元测试 `tests/execution/test_smart_fix_loop.py`：
  - 1 轮成功
  - 3 轮成功
  - 3 轮全失败 → fix_failed=True
  - 不可修错误 → 不调 LLM
  - fix_history 正确传递

## 8. 全失败分支处理

- [x] 8.1 `src/decision/self_consistency.py`：新增 `pick_lightest_failures(candidates) -> List[SQLCandidate]`
  - 按 `ERROR_SEVERITY` 排序
  - 返回最轻一级的所有候选
  - 若最轻级别全是 `UNFIXABLE_ERRORS` → 返回 `[]`
- [x] 8.2 在 Decision 子图中新增 `node_all_failed_fix`：
  - 调用 `pick_lightest_failures`
  - 若返回空 → `fix_failed=True` + 第一个候选的 error
  - 否则逐个候选调用 SmartFix
  - 任一成功立即返回
- [x] 8.3 单元测试 `tests/decision/test_all_failed_branch.py`：
  - 最轻是 SEMANTIC，3 候选，第 2 个成功 → 验证 c3 不被尝试
  - 最轻全是 TIMEOUT → fix_failed=True 且 LLM 调用为 0
  - 最轻是 SYNTAX，全部修不好 → fix_failed=True

## 9. Decision 子图重写

- [x] 9.1 `src/decision/decision_graph.py`：**完全重写** `build_decision_graph`
  - 删除：`node_group_by_result` / `node_find_majority` / `node_select_fastest` / `node_llm_final` / `node_all_failed`
  - 新增：`node_filter_success` / `node_score_by_data` / `node_route_after_r1` / `node_score_by_sql` / `node_route_after_r2` / `node_pick_for_fix` / `node_smart_fix` / `node_all_failed_fix` / `node_verify`
- [x] 9.2 实现路由函数：
  - `route_after_filter`：成功候选数 > 0 → score_by_data；否则 → all_failed_fix
  - `route_after_r1`：唯一=5 → verify；并列=5 → score_by_sql；<5 → pick_for_fix
  - `route_after_r2`：唯一最高或并列 → verify
  - `route_after_pick`：→ smart_fix
  - `route_after_smart_fix`：→ verify（无论 fix_failed 与否）
- [x] 9.3 结果可信度验证 `node_verify`：保留原 `result_verifier` 调用逻辑
- [x] 9.4 集成测试 `tests/decision/test_decision_graph_routes.py`：
  - 验证 6 条路径的完整流程（A/B/C/D/E + 全失败 F/G/H）
  - 验证 `decision_path` 字段记录正确

## 10. 主图集成

- [x] 10.1 `src/graph/main_graph.py`：`make_decision_node` 适配新 DecisionResult 字段
  - 把 `candidate_scores_r1/r2` / `fix_failed` / `last_error` 写入 state
  - emit `final_decision` 事件时包含新字段
- [x] 10.2 验证主图编译通过：`from src.graph.main_graph import build_main_graph; g = build_main_graph(...)`

## 11. API SSE 事件扩展

- [x] 11.1 `src/api/routes/query.py`：在 `event_stream` 中处理新事件类型
  - `score_r1` / `score_r2` / `smart_fix_round`
- [ ] 11.2 更新 API 文档/README 中的事件流说明

## 12. 端到端测试

- [x] 12.1 写 e2e 测试脚本 `tests/e2e/test_optimization_paths.py`：
  - 用 mock LLM + mock 执行结果，跑完 6 条路径
  - 断言每条路径的 LLM 调用次数
- [ ] 12.2 真实数据集回归测试 `tests/e2e/test_bird_sql_regression.py`：
  - 挑 10 个 BIRD 典型 query
  - 对比新旧方案：LLM 调用次数、端到端耗时、最终 SQL 准确率
  - 输出对比报告

## 13. 文档与归档

- [ ] 13.1 更新 `README.md`：决策章节添加"决策 51：两段式评分 + 单候选修复"
- [ ] 13.2 更新 `CLAUDE.md`：架构图反映新流程
- [ ] 13.3 在 `docs/`（如有）补充新流程图（横向版）
- [ ] 13.4 e2e 测试通过后，执行 `openspec archive optimize-sql-correction-loop`

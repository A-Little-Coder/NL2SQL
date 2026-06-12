## Why

当前 NL2SQL Pipeline 在 **Execution + Decision** 阶段存在两个突出问题：

### 问题 1：纠错反思环节耗时严重，LLM 调用浪费

现有逻辑：5 个候选 SQL 各自独立进入 `SQLFixLoop`，每个候选最多重试 2 轮 LLM 修复。最坏情况：

```
5 候选 × (1 次初始执行 + 2 次 LLM 修复 + 2 次重新执行) = 5 次 LLM 生成 + 10 次 LLM 修复 = 15 次 LLM 调用
```

加上 IR/SS/Answerability/Decision 等其他节点的 LLM 调用，单次查询最坏可达 **20+ 次 LLM 调用**，平均响应时间在大型库上常常 30s+。

更深层的问题是：**这种"无差别修复"是低效的**。如果有候选已经能跑出合理结果，去修复其他失败候选并无意义；如果全部失败，逐个修复也存在盲目性。

### 问题 2：模型用英文进行内部思考

Qwen3 的 `reasoning_content`（思考链）会通过 SSE 推送给前端展示。当前虽然 prompt 是中文，但模型内部思考链**绝大多数仍输出英文**，导致前端展示的"模型在想什么"对中文用户不友好，影响产品体验。

### 问题 3：错误类型未做差异化处理

`SQLFixLoop._try_fix` 对所有错误一视同仁——`TIMEOUT_ERROR`、`PERMISSION_ERROR` 这类 LLM 几乎无法修复的错误也会触发 LLM 调用，浪费大量时间和 token。

### 问题 4：评分维度单一

当前 Decision 节点 `llm_final_decision` 把 SQL + 结果 + 执行时间一股脑塞给 LLM 评判，没有区分"数据是否答对了"与"SQL 代码写得好不好"两个独立维度，评分结果易受 SQL 形态干扰。

## What Changes

引入**两段式评分 + 单候选智能修复**架构，重构 Decision 节点和 SmartFix 子图：

### 1. ExecuteAll：一次性执行不修复

新增 `ExecuteAllNode` —— 5 个候选只做**一次性执行**，不在执行阶段触发任何 LLM 修复。结果回填到 `sql_candidates`。

### 2. ScoreByData (R1)：第一轮·数据视角评分

新增 `ScoreByDataNode` —— 对**所有执行成功**的候选评分（0-5 分）。

- **输入**：用户 query + 每候选的【数据结果】（列名 + top-20 行 + cell 截断 20 字符 + 行数 + 执行时间）
- **隐藏 SQL 代码**，强制 LLM 仅从数据视角判断
- **prompt 明示**"结果为 top-20 节选"，避免误判行数

### 3. ScoreBySQL (R2)：第二轮·SQL 视角评分（条件触发）

新增 `ScoreBySQLNode` —— **仅在 R1 出现并列最高分=5 时触发**。

- **输入**：用户 query + 各候选 SQL 代码 + 执行时间 + R1 评分及评价
- 使用【严格模式评分标准】(0-5 分)

### 4. 决策路由

```
R1 唯一=5             → 直接返回
R1 并列=5             → R2 → 唯一最高返回 / 并列选最快返回
R1 < 5（唯一/并列）   → 选最高分（并列选最快）→ SmartFix
```

### 5. SmartFix：单候选 ≤3 轮智能修复

重构 `SQLFixLoop` —— **仅修复 1 个候选**（评分选出的最优者），最多 3 轮。

- 每轮 prompt 带 `fix_history`（历次修复 SQL + 错误），避免反复绕同样的坑
- **3 轮失败 → `fix_failed=True`**，返回最佳 SQL + 最后报错（不阻塞 downstream）

### 6. 全失败分支：按错误等级逐个修

5 候选全失败时：

- 按错误等级排序：`SEMANTIC < SYNTAX < UNKNOWN < TIMEOUT < RUNTIME < PERMISSION`
- 取**最轻一级的所有候选**，逐个进入 SmartFix
- **任一成功立即返回**，剩余不修
- 若最轻级别全是 `TIMEOUT/RUNTIME/PERMISSION` → 直接 `fix_failed=True`（不浪费 LLM 调用）

### 7. Decision 节点完全重写

`build_decision_graph` 删除 `group_by_result` / `find_majority` / `select_fastest` / `llm_final_decision`，重写为评分驱动的新结构。`result_verifier`（结果可信度验证）**保留**作为收尾节点。

### 8. 中文思考指令注入（顺手优化）

在 `utils/llm_client.py` 统一注入一条 system-level 指令"请全程使用中文进行内部思考和推理"，**所有 9 个业务 prompt 全部不动**。修复 Qwen3 reasoning_content 出英文的问题。

## Impact

### Affected Specs

- `execution-engine`：去除候选级别的修复循环（改为 ExecuteAll 一次性执行）
- `self-consistency-decision`：删除多数投票分组逻辑，替换为两段式评分 + SmartFix
- 新增 `smart-fix-loop`：单候选 ≤3 轮智能修复 + fix_history 上下文传递
- 新增 `llm-thinking-language`：中文思考指令注入

### Affected Code

| 文件 | 改动 |
|-----|------|
| `src/graph/main_graph.py` | 重写 `make_execution_node`、`make_decision_node`；新增评分相关节点工厂 |
| `src/execution/execution_graph.py` | 删除循环修复，改为单次执行子图 |
| `src/execution/executor.py` | `SQLFixLoop` 重构：单候选 3 轮 + fix_history + 错误类型过滤 |
| `src/decision/decision_graph.py` | **完全重写** —— 新结构：score_by_data → (条件) score_by_sql → (条件) smart_fix → verify |
| `src/decision/self_consistency.py` | 新增 `score_by_data` / `score_by_sql` 方法；删除 `group_by_result` / `find_majority_group` / `llm_final_decision` |
| `src/graph/state.py` | 新增字段：`candidate_scores_r1` / `candidate_scores_r2` / `selected_candidate_id` / `fix_failed` / `fix_rounds_used` / `last_error` |
| `utils/llm_client.py` | 新增中文思考指令注入（system message 追加） |
| `src/api/routes/query.py` | SSE 事件类型新增 `score_r1` / `score_r2` / `smart_fix_round`（业务事件流） |

### Performance Impact

| 场景 | 当前 LLM 调用（最坏） | 新方案（最坏） | 降幅 |
|------|---------------------|--------------|------|
| 直接返回（R1=5 唯一） | 15 | 1 | -93% |
| R1=5 并列 → R2 选最优 | 15 | 2 | -87% |
| R1<5 → SmartFix 成功 | 15 | 2~4 | -73% |
| R1<5 → SmartFix 3 轮失败 | 15 | 4 | -73% |
| 全失败 → 逐个修成功 | 15 | 1~9 | -40% ~ -93% |
| 全失败 → 全部修不好 | 15 | ≤3×N (N=最轻候选数) | 视 N |

**平均情况预计 LLM 调用降低 70% 以上**，端到端响应时间预计降低 50%+。

### Breaking Changes

- `DecisionResult` 字段扩展（向后兼容：原字段全部保留，仅新增字段）
- API SSE 事件流新增 `score_r1` / `score_r2` / `smart_fix_round` 三类业务事件
- `NL2SQLState` 新增多个字段（向后兼容：通过 `state.get(...)` 访问，旧调用方不受影响）

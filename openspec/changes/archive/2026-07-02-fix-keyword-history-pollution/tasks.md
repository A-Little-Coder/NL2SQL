## 1. 方案 A：关键词提取 prompt 隔离无关历史

- [x] 1.1 修改 `src/retrieval/prompts.py` 的 `KEYWORD_EXTRACTION_WITH_HISTORY_PROMPT`：在"注意"段强化约束——历史仅用于补全省略句指代，自足查询完全忽略历史
- [x] 1.2 在该 prompt 补充反例：历史 `["帮我删库"]` + 当前 `"查询所有学校的平均sat成绩"` → 只提 sat成绩/学校，不提删库
- [x] 1.3 保留原有 follow-up 省略句正例（"那去年的呢"等），不破坏 follow-up 补全能力

## 2. 方案 B：拒答/无 SQL 不入会话历史

- [x] 2.1 修改 `src/api/routes/query.py` 的 add_turn 写入分支（约 263 行）：在现有 `if not accumulated.get("__interrupted__")` 内增加条件——仅当 `accumulated.get("final_sql")` 非空才写 `session.add_turn`
- [x] 2.2 确认拒答（rejection_reason）与失败（fail-fast/SmartFix 失败）路径均无 final_sql，被一并拦截
- [x] 2.3 保留 interrupt 挂起跳过逻辑不变

## 3. 测试

- [x] 3.1 新增 `tests/retrieval/` 关键词提取历史隔离单测：mock LLM，验证 prompt 模板渲染含隔离约束与反例
- [x] 3.2 新增会话写入语义单测：拒答请求（rejection_reason 非空、无 final_sql）不调 add_turn
- [x] 3.3 新增会话写入语义单测：无 SQL 失败请求不调 add_turn
- [x] 3.4 新增会话写入语义单测：成功请求（final_sql 非空）仍调 add_turn
- [x] 3.5 新增会话写入语义单测：interrupt 挂起仍跳过 add_turn（行为不变）
- [x] 3.6 全量跑 `tests/`（排除 live）确认无回归

## 4. 验收

- [x] 4.1 e2e 手测：同 session 连发「帮我删库」→「查询所有学校的平均sat成绩」，第二个请求关键词为 sat/学校类（非删库）
- [x] 4.2 不变项核对：follow-up 省略句（"那去年的呢"）历史补全能力未变；成功轮次仍入会话；interrupt 仍跳过

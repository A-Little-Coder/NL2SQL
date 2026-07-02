## 1. 配置与数据模型

- [x] 1.1 新增 SessionMemory recall 配置，包含 `dense_top_k`、`bm25_top_k`、`rrf_k`、`rrf_threshold`、`require_multi_channel_hit=false`
- [x] 1.2 定义历史记忆召回结果数据结构，包含 `historical_query`、`historical_sql`、`rrf_score`、`dense_rank`、`bm25_rank`、`conversation_id`、`turn_id`
- [x] 1.3 扩展主图 state，新增可选的历史 SQL 弱参考字段，供 CG 阶段读取

## 2. SessionMemory 两层存储

- [x] 2.1 新增 query recall index 抽象，支持写入成功 query、按 metadata 过滤、按向量召回
- [x] 2.2 实现 demo 版 Chroma query recall index，使用 BGE-M3 embedding 写入 query 向量
- [x] 2.3 新增 conversation store 抽象，支持按 `conversation_id` / `turn_id` 写入和读取无结果历史对话
- [x] 2.4 实现 demo 版 JSON conversation store，保存 query、final_sql、user_id、session_id、db_id、turn_id、timestamp，不保存结果数据和中间 state
- [x] 2.5 确保 query recall index metadata 包含 `user_id`、`session_id`、`db_id`、`success`、`conversation_id`、`turn_id`、`final_sql`

## 3. BM25 与 RRF 混合召回

- [x] 3.1 实现本地 BM25 或轻量 BM25 召回组件，召回范围限定为当前 `user_id/session_id/db_id/success=true`
- [x] 3.2 实现 RRF ranker，支持 dense rank、BM25 rank、单路命中候选和 `rrf_threshold` 过滤
- [x] 3.3 新增 `HybridSessionRetriever`，串联 session 过滤、dense recall、BM25 recall、RRF 融合和 conversation store 回表
- [x] 3.4 实现 RRF 命中后的历史会话裁剪策略，首版保留命中 turn 及前后一轮 query/sql 摘要

## 4. HistoryCache 与主流程接入

- [x] 4.1 修改 `history_cache` 节点，在现有 LLM 可复用判断前调用 `HybridSessionRetriever`
- [x] 4.2 当 HistoryCache 判断可复用时，沿用现有 `cache_hit=true` 与 `cached_sql` 流程进入 Execution 重新执行
- [x] 4.3 当 HistoryCache 判断不可复用时，将召回结果裁剪为历史 `query + final_sql` 弱参考写入 state
- [x] 4.4 修改 CG 阶段 Prompt，允许读取历史 SQL 弱参考，并明确不得使用当前 selected schema 之外的表和列
- [x] 4.5 增加异常降级逻辑：query recall index、BM25、conversation store 任一异常时不影响主 NL2SQL 流程

## 5. MemoryUpdater 写入策略

- [x] 5.1 修改 MemoryUpdater，在查询成功后写入 query recall index 和 conversation store
- [x] 5.2 实现 success-only 判断：无 final_sql、执行失败、拒答、有 error、结果验证不可信时不写入召回库
- [x] 5.3 保留现有 SessionMemory 当前会话 JSON 写入能力，新增 v2 recall 写入不破坏旧格式

## 6. UserMemory schema 治理

- [x] 6.1 固化 UserMemory 顶层 topics：`term_preferences`、`frequently_used_tables`、`metric_definitions`、`query_preferences`、`domain_context`、`clarification_history`
- [x] 6.2 加载旧 UserMemory 文件时自动补齐缺失 topic，保存时过滤未知顶层 key
- [x] 6.3 修改 UserMemory 总结/更新逻辑，要求 LLM 输出 topic-specific patch，由代码合并到 JSON
- [x] 6.4 增加 few-shot 过滤逻辑，禁止将 few-shot examples、示例 SQL 列表、结果数据、中间 graph state 写入 UserMemory

## 7. 单元测试

- [x] 7.1 测试 query recall index 只写入成功 query，失败/拒答/无 SQL 不写入
- [x] 7.2 测试 SessionMemory recall 必须按当前 `user_id/session_id/db_id/success=true` 过滤，不返回其他 session 的相似 query
- [x] 7.3 测试 RRF ranker 支持双路命中、仅 dense 命中、仅 BM25 命中和低于阈值丢弃
- [x] 7.4 测试 HybridSessionRetriever 能根据 RRF 结果回表读取 conversation store
- [x] 7.5 测试 HistoryCache 可复用时复用历史 SQL，不可复用时只产出历史 query/sql 弱参考
- [x] 7.6 测试 CG 阶段接收历史 SQL 弱参考时仍受 selected schema 约束
- [x] 7.7 测试 UserMemory 固定 topic schema：缺失补齐、未知 key 过滤、few-shot 和结果数据不落盘

## 8. 集成验证与文档

- [x] 8.1 编写主图级 mock 集成测试，验证 `history_cache → execution` 的可复用路径
- [x] 8.2 编写主图级 mock 集成测试，验证 `history_cache → ir → ss → cg` 的不可复用弱参考路径
- [x] 8.3 编写同 session follow-up demo，验证本 session 内相似 query 可通过 RRF 命中历史记忆
- [x] 8.4 更新 README 或 docs，说明 SessionMemory v2 的两层存储、RRF 阈值、success-only 写入和 UserMemory schema 规则
- [x] 8.5 运行相关单元测试和全量测试，确保新增/修改测试通过

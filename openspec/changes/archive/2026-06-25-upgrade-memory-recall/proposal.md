## Why

当前 SessionMemory 主要按 `session_id` 读取最近几轮对话，HistoryCache 的候选来源较窄，难以在同一会话内稳定复用历史成功查询的 SQL 经验。同时，UserMemory 虽已使用 JSON 存储，但长期记忆的写入边界需要进一步收敛，避免 LLM 自由新增字段或把 few-shot 示例混入用户偏好记忆。

本变更将 SessionMemory 升级为“本 session 内 query 混合召回 + HistoryCache 复用判断”的两层记忆结构，并固化 UserMemory 的预定义 topic schema，使历史记忆更可控、低噪声、可验证。

## What Changes

- 新增 SessionMemory 两层存储方案：
  - 第一层为 query recall index：对成功查数的历史 query 使用 BGE-M3 向量化，demo 阶段写入 Chroma，生产可替换为 ES 向量索引。
  - 第二层为 conversation store：保存完整历史对话但不包含查询结果，demo 阶段使用 JSON 文件，生产可替换为 Hive。
- SessionMemory 召回时必须先按当前 `user_id`、`session_id`、`db_id`、`success=true` 过滤候选，再在本 session 范围内执行向量召回与 BM25 召回。
- 新增 Dense Vector Recall + BM25 Recall 的 RRF（Reciprocal Rank Fusion）融合排序机制：
  - 允许单路召回命中；
  - dense / BM25 候选取并集；
  - 只根据融合后的 `rrf_score >= rrf_threshold` 判断是否召回历史记忆。
- HistoryCache 接入 RRF 召回结果：
  - 可复用时按现有流程复用历史 SQL 并重新执行；
  - 不可复用时只保留历史 `query` 和 `final_sql` 作为后续 SQL 生成弱参考，丢弃中间过程和结果数据。
- MemoryUpdater 只将正常查数成功的对话写入 SessionMemory recall index 和 conversation store；失败、拒答、无 SQL、不可验证结果不参与召回。
- UserMemory 明确继续使用 JSON 存储，并固化预定义 topics：常用表、指标定义、查询习惯、术语偏好、领域上下文、澄清历史等。
- UserMemory 更新逻辑必须按预定义 topics 输出和合并，不允许 LLM 自由新增顶层 key。
- UserMemory 不再保存 few-shot 示例，避免与 SQLGenerator 的示例选择机制重复和污染。

## Capabilities

### New Capabilities
- `session-memory-hybrid-recall`: 定义本 session 内成功历史 query 的向量召回、BM25 召回、RRF 融合、阈值过滤、conversation store 回表和 HistoryCache 复用判断行为。
- `user-memory-schema-governance`: 定义 UserMemory 的固定 JSON topic schema、受控更新边界，以及禁止保存 few-shot 示例和结果数据的行为。

### Modified Capabilities

无。

## Impact

- 影响 `src/memory/`：新增 session recall index、conversation store、hybrid retriever、RRF ranker 等组件；修改 `history_cache.py` 和 `memory_updater.py` 的接入逻辑。
- 影响主图状态与节点：`history_cache` 节点需要先执行本 session 历史召回，再进行 SQL 可复用判断；不可复用时需要向后续 CG 阶段传递弱参考 SQL。
- 影响 SQL 生成：CG Prompt 可接收历史 `query + final_sql` 弱参考，但不得突破当前 selected schema 约束。
- 影响配置：新增 SessionMemory recall 相关配置，如 dense_top_k、bm25_top_k、rrf_k、rrf_threshold、require_multi_channel_hit=false。
- 影响测试：新增 RRF、success-only indexing、本 session 过滤、HistoryCache 可复用/不可复用、UserMemory schema 越界和 few-shot 排除等测试。
- demo 阶段不引入生产 ES/Hive 依赖；使用 Chroma + JSON 文件实现可替换抽象。

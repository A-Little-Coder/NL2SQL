## Context

当前记忆系统已经具备三类基础能力：

- `SessionMemory`：按 `user_id/session_id` 将会话轮次持久化到 JSON 文件，主要服务当前会话最近几轮上下文。
- `HistoryCache`：在 IR 前判断当前 query 是否能复用历史 SQL；命中后只复用 SQL，不复用历史结果。
- `UserMemory`：以 JSON 保存用户长期记忆，包括术语偏好、常用表、指标定义、查询习惯、领域上下文、澄清历史。

现有问题是：

1. SessionMemory 的读取方式偏“最近 N 轮”，缺少本 session 内基于 query 相似度的主动召回。
2. HistoryCache 的候选来源受限，无法充分利用本 session 内较早的成功查询。
3. 失败查询和成功查询需要在长期召回层明确隔离，避免错误 SQL 参与复用判断。
4. UserMemory 虽然已有固定结构，但总结/更新边界需要继续收敛，避免 few-shot 示例和自由字段污染长期用户偏好。

本设计不改变 NL2SQL 主流程的核心顺序，重点升级 `history_cache` 前后的记忆候选来源和写入策略。

## Goals / Non-Goals

**Goals:**

- 将 SessionMemory 扩展为两层结构：query recall index + conversation store。
- 在本 session 内执行 Dense Vector Recall + BM25 Recall，并用 RRF 融合排序。
- 允许单路召回命中；只用融合后的 `rrf_score` 进行阈值过滤。
- 只索引正常查数成功的历史 query，失败、拒答、无 SQL 的轮次不参与召回。
- 召回命中后继续使用现有 HistoryCache 判断 SQL 是否可复用。
- 不可复用时，仅把历史 `query + final_sql` 作为 SQL 生成弱参考，不携带中间过程和结果数据。
- 固化 UserMemory JSON topics，禁止 LLM 新增顶层 key 或保存 few-shot 示例。
- demo 阶段使用 Chroma + JSON 文件；保留未来替换为 ES + Hive 的抽象边界。

**Non-Goals:**

- 不做跨 session 或跨用户历史召回。
- 不复用历史查询结果；历史 SQL 命中后仍然重新执行。
- 不把历史 IR/SS/CG/Decision 中间状态注入新流程。
- 不引入生产 ES/Hive 依赖；本期只实现可替换接口和 demo 存储。
- 不重做 SQLGenerator 的 curated few-shot 选择器。
- 不修改数据库 schema 或外部 API 请求/响应协议，除非内部 state 需要增加历史参考字段。

## Decisions

### 1. SessionMemory 使用两层存储

采用两层结构：

```text
┌──────────────────────────────────────────────┐
│ Query Recall Index                            │
│ - query 文本                                   │
│ - BGE-M3 embedding                            │
│ - user_id/session_id/db_id/turn_id             │
│ - conversation_id                              │
│ - success                                      │
│ - final_sql                                    │
└──────────────────┬───────────────────────────┘
                   │ id
                   ▼
┌──────────────────────────────────────────────┐
│ Conversation Store                            │
│ - 完整历史对话                                 │
│ - 不含查询结果                                 │
│ - demo: JSON                                   │
│ - production: Hive                             │
└──────────────────────────────────────────────┘
```

**选择原因：** query index 负责快速召回，conversation store 负责保存可读历史，两者职责清晰。索引层可以从 Chroma 切换到 ES，存储层可以从 JSON 切换到 Hive。

**替代方案：** 直接在 SessionMemory JSON 中遍历全部 turn 做相似度计算。该方案实现简单，但无法模拟生产向量索引，也不利于后续 ES/Hive 替换。

### 2. 召回范围限定在当前 session

召回必须先过滤：

```text
user_id == 当前 user_id
session_id == 当前 session_id
db_id == 当前 db_id
success == true
```

之后才执行向量召回和 BM25 召回。

**选择原因：** SessionMemory 的定位是会话级短期记忆，不应跨 session 引入历史上下文。这样能降低噪声、避免不同任务主题互相污染，并减少权限隔离风险。

**替代方案：** 按 user_id 跨 session 召回。该方案能复用更多历史经验，但更容易把不同时间、不同上下文、不同数据库的 SQL 引入当前会话，暂不采用。

### 3. Dense + BM25 候选使用 RRF 融合，并允许单路召回

召回过程：

```text
当前 query
  │
  ├── Dense Vector Recall top_k
  ├── BM25 Recall top_k
  ▼
候选并集
  ▼
RRF Fusion
  ▼
rrf_score >= rrf_threshold 才召回
```

RRF 公式：

```text
rrf_score(doc) = Σ 1 / (rrf_k + rank_i(doc))
```

其中 `i ∈ {dense, bm25}`。如果某个候选只在一路召回中命中，另一召回通道不贡献分数，但该候选仍可参与最终阈值判断。

推荐 demo 配置：

```yaml
session_memory:
  recall:
    dense_top_k: 10
    bm25_top_k: 10
    rrf_k: 60
    rrf_threshold: 0.015
    require_multi_channel_hit: false
```

**选择原因：** 允许单路召回能兼顾语义改写和关键词强匹配场景；RRF 负责融合排序，HistoryCache 继续负责最终可复用判断。

**替代方案：** 要求 dense 和 BM25 双路同时命中。该方案更保守，但会漏掉词面差异较大的 follow-up 或语义改写。

### 4. HistoryCache 仍然是 SQL 复用的唯一决策点

RRF 命中只表示“历史候选值得检查”，不直接复用 SQL。命中历史会话后，仍调用现有 HistoryCache：

```text
RRF recalled history
  ▼
HistoryCache.check(...)
  ├── hit=true  → 使用 cached_sql 进入 Execution 重新执行
  └── hit=false → 不复用 SQL，继续标准 NL2SQL 流程
```

**选择原因：** 召回相似不等于业务等价，尤其指标口径、时间范围、粒度可能不同。LLM 判断仍是安全边界。

### 5. 不可复用历史只作为 CG 弱参考

当 HistoryCache 判断不可复用时，系统只保留：

```text
historical_query
historical_sql
rrf_score
dense_rank
bm25_rank
source_turn_id
```

这些历史参考只允许进入 SQL Generation 阶段，不能注入 IR/SS 阶段。

CG Prompt 需要明确约束：历史 SQL 仅供写法和口径参考，不得使用当前 selected schema 之外的表和列。

**选择原因：** IR/SS 已经通过当前 query 和当前 schema 的数学方法召回，过早注入历史 SQL 容易带偏召回。CG 阶段弱参考可以复用 SQL 写法，但仍受 selected schema 约束。

### 6. 成功才写入 SessionMemory recall index

写入条件建议统一为：

```text
final_sql 存在
AND execution 成功
AND rejection_reason 为空
AND error 为空
AND 结果验证可信或未触发拒答
```

失败、拒答、无 SQL、执行错误、验证不可信的轮次不写入 query recall index，也不参与 BM25 索引。

**选择原因：** 历史召回库应只保存可复用的成功经验，避免失败 SQL 污染后续判断。

### 7. UserMemory 固定 JSON topics，代码合并更新

UserMemory 顶层 key 固定为：

```text
term_preferences
frequently_used_tables
metric_definitions
query_preferences
domain_context
clarification_history
```

LLM 只输出结构化 update patch，代码负责 merge。任何不在预定义 schema 内的顶层 key 都必须被忽略或拒绝。

**选择原因：** 长期记忆应该是稳定用户画像和偏好，不应让 LLM 自由扩展结构。

### 8. few-shot 不进入 UserMemory

few-shot 示例继续由 SQLGenerator 的示例选择器管理。UserMemory 不保存 few-shot examples、完整历史 SQL 列表或结果数据。

**选择原因：** few-shot 是模型生成策略，UserMemory 是用户偏好。混合存储会导致 Prompt 膨胀、示例重复和长期记忆污染。

## Risks / Trade-offs

- **RRF 阈值不好直觉配置** → 使用默认值 `0.015` 起步，并通过单元测试覆盖单路命中、双路命中、低分丢弃；后续可用日志观测调整。
- **Chroma 不支持 BM25** → demo 中 BM25 使用本地 JSON/BM25 索引实现，生产切换 ES 时由 ES 承担 BM25 能力。
- **历史 SQL 可能与当前 schema 不一致** → 可复用 SQL 必须重新执行；弱参考 SQL 进入 CG 时必须受 selected schema 约束。
- **本 session 召回候选过少** → 这是会话记忆的有意边界；跨 session 经验应由 UserMemory 的指标定义/术语偏好承担，不在本变更扩展。
- **长会话完整历史过长** → Conversation Store 可以保存完整历史，但传给 HistoryCache/CG 时只裁剪命中 turn 及必要 query/sql 摘要。
- **失败查询完全不入库可能丢失纠错经验** → 本期优先保证召回库质量；失败样本如需分析应进入 debug/audit 日志，而不是复用候选库。

## Migration Plan

1. 保留现有 `SessionMemory` JSON 文件格式，新增 v2 recall index 和 conversation store，不破坏旧读取逻辑。
2. `history_cache` 节点优先尝试 v2 召回；无候选时回退到当前最近 N 轮历史逻辑。
3. 新查询成功后开始写入 v2 索引；旧历史不强制迁移。
4. 如果 v2 索引或 BM25 组件异常，安全降级为不命中历史缓存，继续完整 NL2SQL 流程。
5. UserMemory 读取时对缺失 key 自动补齐默认结构；保存时过滤未知顶层 key。

## Open Questions

- `rrf_threshold=0.015` 是否作为首版默认值，还是需要通过少量真实会话样本标定后再调整？
- Conversation Store 传给 HistoryCache 时是否只保留命中 turn，还是保留命中 turn 前后一轮上下文？首版建议保留命中 turn 前后一轮。
- BM25 demo 是否引入 `rank-bm25` 依赖，还是先实现轻量本地 tokenizer + 简单 BM25？首版建议优先使用轻量本地实现，避免新增依赖。

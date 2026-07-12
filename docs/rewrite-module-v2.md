# Rewrite 模块设计文档

> 版本：v2（重构版）
> 对应 Change：`rewrite-before-cache`

## 1. 设计目标

在 NL2SQL 流水线中，用户原始查询可能包含**指代、歧义、对象缺失**等问题，需要经过改写才能进入后续流水线。同时需要**前置拦截**写操作等违规查询，以及通过**反问澄清**让用户补充缺失信息。

### 职责划分

| 节点 | 职责 | 说明 |
|------|------|------|
| **前置拒答检测** | 硬性违规检测 | 写操作、空查询等，不调 LLM |
| **Rewrite 子图** | 指代消解 + 改写 + 反问澄清 | 检测问题 → 改写 → 再检测 → 反问 → 改写... |
| **TaskDecomposer** | 意图拆解 | 只做单意图/多意图分解，不再有反问/拒答 |

## 2. 节点流程图

```
START
  │
  ▼
┌──────────────────────────────────────────────┐
│ [前置拒答检测]                                │
│  · 空查询 → 拒答 END                          │
│  · 写操作关键词 → 拒答 END                    │
│  · 正常 → 放行                                │
└──────────────────┬───────────────────────────┘
                   │
         ┌─────────┴──────────┐
         │ 违规（拒答）        │ 正常
         ▼                    ▼
        END                  ┌──────────────────────────────────────┐
                             │ Rewrite 子图                         │
                             │                                      │
                             │  ┌──────────────────┐                │
                             │  │ [问题检测]         │               │
                             │  │ LLM 检测指代/歧义/ │               │
                             │  │ 对象缺失           │               │
                             │  └────────┬─────────┘               │
                             │           │                          │
                             │    ┌──────┴──────┐                   │
                             │    │ 无问题       │ 有问题            │
                             │    ▼              ▼                  │
                             │  输出到       ┌──────────────────┐   │
                             │  下游         │ [改写执行]         │   │
                             │   ↓           │ LLM 用前5轮上下文  │   │
                             │  ┌──────────┐ │ 改写 query         │   │
                             │  │History    │ └────────┬─────────┘   │
                             │  │Cache      │          │              │
                             │  │…→ END     │          ▼              │
                             │  └──────────┘   ┌──────────────────┐  │
                             │                 │ [问题检测]（再检测）│  │
                             │                 └────────┬─────────┘  │
                             │                          │             │
                             │           ┌──────────────┼──────────┐  │
                             │           │ 无问题        │ 仍有问题  │  │
                             │           ▼              ▼          │  │
                             │      输出到下游    ┌──────────────┐  │  │
                             │       ↓           │改写次数 < 2？ │  │  │
                             │                  └──┬───────┬────┘  │  │
                             │                     │       │       │  │
                             │                    是       否      │  │
                             │                     │       │       │  │
                             └─────────────────────┘       │       │  │
                                                           ▼       │  │
                                              ┌──────────────────┐  │  │
                                              │ [反问澄清]         │  │  │
                                              │ interrupt 挂起     │  │  │
                                              │ 等待用户补充信息    │  │  │
                                              │ （可多次反问）      │  │  │
                                              └────────┬─────────┘  │  │
                                                       │            │  │
                                              ┌────────┴────────┐   │  │
                                              │ 用户补充信息     │   │  │
                                              │ 放入上下文       │   │  │
                                              └────────┬─────────┘   │  │
                                                       │             │  │
                                                       ▼             │  │
                                              ┌──────────────────┐   │  │
                                              │ [改写执行]         │   │  │
                                              │ 用新上下文再改写   │   │  │
                                              └────────┬─────────┘   │  │
                                                       │             │  │
                                                       ▼             │  │
                                              ┌──────────────────┐   │  │
                                              │ [问题检测]         │───┘  │
                                              │ 通过 → 输出到下游  │      │
                                              │ 仍有问题 → 继续反问│      │
                                              └──────────────────┘      │
                                                                       │
                             └─────────────────────────────────────────┘
```

## 3. 节点详细设计

### 3.1 前置拒答检测节点（PreReject）

**位置**：START 之后第一个节点

**职责**：只做硬性违规检测，不调 LLM

**检测规则**：
- 空查询 / 纯空白 → 拒答
- SQL 写操作关键词（`insert`/`update`/`delete`/`drop`/`create`/`alter`/`truncate`/`replace`/`merge`/`grant`/`revoke`，带词边界）
- 中文写操作意图词（`删除`/`修改`/`更新`/`插入`/`清空`/`建表`/`删除表`/`添加数据`/`改数据`/`删数据`）

**输出**：
- 违规 → `rejection_reason` + `rewrite_rejection_reason` → END（拒答写入会话历史）
- 正常 → 放行到 Rewrite 子图

### 3.2 Rewrite 子图

**位置**：前置拒答检测之后、HistoryCache 之前

**结构**：包含两个子节点 + 条件边循环

#### 子节点 A：问题检测（DetectIssues）

**输入**：`user_query` + `conversation_history`（前 5 轮）

**输出**：检测结果（verdict: pass | has_issues）

**检测内容**：
- **指代**：查询含代词（它、那、其、该、此等）或省略成分（"那去年的呢"、"只看北京的呢"）
- **歧义**：查询含多义实体（如"苹果"可指公司或水果）
- **缺失**：查询缺少关键限定（如"查销售额"缺公司/时间）

**判定规则**：
- 无问题 → PASS → 放行到 HistoryCache
- 有问题 → 进入改写执行

#### 子节点 B：改写执行（RewriteExecute）

**输入**：`user_query` + `conversation_history`（前 5 轮）

**输出**：改写后的 `rewritten_query` + `rewrite_reason`

**改写策略**：
- 利用会话历史补全指代/歧义/缺失信息
- 改写后保持原意，不改变用户原本意图
- 每次改写 emit `rewrite` SSE 事件（含 `rewritten_query`、`rewrite_reason`、`rewrite_round`）

#### 循环逻辑

**改写循环（不反问）**：
```
[问题检测] → 有问题 → [改写执行] → [问题检测] → 仍有问题 → 继续改写
                                                        ↓
                                                  改写次数 < 2？ → 是 → 继续
                                                  改写次数 ≥ 2？ → 否 → 触发反问
                                                        ↓
                                                  [问题检测] → 无问题 → 输出到下游
```

**反问循环（可多次）**：
```
[问题检测] → 2次改写仍有问题 → [反问澄清] → interrupt 挂起
                                               ↓
                                         用户补充信息
                                               ↓
                                         [改写执行]（用新上下文）
                                               ↓
                                         [问题检测]
                                               ↓
                                        通过 → 输出到下游
                                        仍有问题 → 继续反问
```

### 3.3 反问澄清节点（Clarify）

**位置**：Rewrite 子图内，当 2 次改写后仍有问题时触发

**机制**：`interrupt` 挂起，等待用户补充信息

**输出**：用户补充的信息放入改写上下文，继续改写循环

**反向上限**：可多次反问，直到检测通过

### 3.4 TaskDecomposer（精简版）

**位置**：HistoryCache 之后

**职责**：只做意图拆解（单意图/多意图），不再有反问/拒答能力

**输入**：已是 Rewrite 改写后的完整语义 query

**裁决**：
- 单意图 → `run_single_query`
- 多意图 → `run_subqueries`

## 4. 条件边路由

### 4.1 前置拒答检测 → 重写/END

```
route_after_pre_reject:
  rewrite_rejection_reason 非空 → END
  正常 → rewrite
```

### 4.2 Rewrite 子图内条件边

```
route_after_detect_issues:
  pass → history_cache
  has_issues → rewrite_execute

route_after_rewrite_execute:
  → detect_issues（循环回检测）

route_after_second_detect:
  pass → history_cache
  has_issues + rewrite_round < 2 → rewrite_execute
  has_issues + rewrite_round >= 2 → clarify
```

### 4.3 反问后条件边

```
route_after_clarify:
  → rewrite_execute（带用户补充信息）
```

## 5. 状态字段

### 新增字段

```python
# 前置拒答检测
rejection_reason: Optional[str]      # 主流程拒答原因
rewrite_rejection_reason: Optional[str]  # Rewrite 拒答原因（写操作等）

# Rewrite 子图
rewritten_query: str                 # 改写后的查询
rewrite_reason: str                  # 改写说明
rewrite_round: int                   # 当前改写轮次

# 反问澄清
clarify_question: Optional[str]      # 反问问题
clarify_round: int                   # 反问轮次
clarify_context: Optional[str]       # 用户补充的信息（用于改写上下文）
```

### 字段生命周期

```
START → pre_reject(设 rejection_reason) → END（拒答）
       → rewrite(设 rewritten_query/rewrite_round/rewrite_reason) 
         → clarify(设 clarify_question/clarify_round/clarify_context)
           → rewrite(用clarify_context更新history再改写)
         → history_cache(使用rewritten_query作为user_query)
```

## 6. 与 TaskDecomposer 的分工

| 能力 | 归属 | 说明 |
|------|------|------|
| 写操作检测 | 前置拒答检测 | 硬性关键词匹配，不调 LLM |
| 指代消解 | Rewrite 子图 | 利用会话历史补全 |
| 歧义处理 | Rewrite 子图 | 利用会话历史区分 |
| 缺失补全 | Rewrite 子图 | 利用会话历史补全 |
| 反问澄清 | Rewrite 子图 | 改写无法解决时反问用户 |
| 意图拆解 | TaskDecomposer | 只做单/多意图判断 |
| 多意图编排 | TaskDecomposer | 子查询列表 + Orchestrator 执行 |

## 7. 会话历史记录

### 写入规则

与当前一致（`_should_write_session_turn`）：

- 前置拒答检测拦截 → 写入会话历史（供后续轮次理解为何被拒）
- Rewrite 反问挂起 → 不写入（等 resume 完成后再写）
- 正常完成 → 写入会话历史

### 历史格式

```python
{
    "user_query": "原始用户查询",
    "rewritten_query": "改写后的查询（如有）",
    "final_sql": "最终 SQL",
    "rejection_reason": "拒答原因（如有）",
    "rewrite_rejection_reason": "Rewrite 拒答原因（如有）",
    "cache_hit": False,
    "db_id": "california_schools",
}
```

## 8. SSE 事件

| 事件类型 | 触发节点 | 字段 |
|----------|----------|------|
| `rewrite` | 改写执行 | `{rewritten_query, rewrite_reason, rewrite_round}` |
| `clarification` | 反问澄清 | `{question, round, awaiting_answer}` |
| `stage` | 各节点 | `{node, status: started/done}` |
| `error` | 拒答 | `{error, rejection: true}` |
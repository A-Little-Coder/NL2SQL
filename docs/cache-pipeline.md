# 缓存流水线：Rewrite → HistoryCache → ValueRewrite → CacheConfirm

> 版本: 0.1.0 · 更新日期: 2026-07-14
>
> 本文件专门讲解从 LLM 改写 → 缓存命中检测 → 值参数改写 → 反问确认这**连续四步**的完整流水线逻辑。
> 重在展示"状态如何流转""LLM 在每一步分别做什么""各步骤如何衔接"。
>
> 配套阅读：
> - [rewrite-module-v2.md](./rewrite-module-v2.md) — Rewrite 子图内部细节（问题检测/改写循环/反问澄清）
> - [history-cache.md](./history-cache.md) — HistoryCache 节点内部细节（召回/LLM 判断/安全边界）
> - [nl2sql-workflow.md](./nl2sql-workflow.md) — 完整主图流程

---

## 目录

1. [整体定位](#1-整体定位)
2. [流水线总览图](#2-流水线总览图)
3. [Step 1: Rewrite（LLM 改写子图）](#3-step-1-rewrite-llm-改写子图)
4. [Step 2: HistoryCache（缓存命中检测）](#4-step-2-historycache-缓存命中检测)
5. [Step 3: ValueRewrite（值参数改写）](#5-step-3-valuerewrite-值参数改写)
6. [Step 4: CacheConfirm（反问确认）](#6-step-4-cacheconfirm-反问确认)
7. [状态字段完整流转](#7-状态字段完整流转)
8. [反常路径分析](#8-反常路径分析)
9. [关键文件索引](#9-关键文件索引)

---

## 1. 整体定位

这四步构成了主图**最前端的加速流水线**：

```
START → pre_reject → rewrite → history_cache → value_rewrite → cache_confirm → run_single_query
```

设计意图是：**尽可能用历史知识加速当前查询**。如果命中缓存，可以跳过 IR/SS/CG/Decision 等昂贵环节，直接执行 SQL 返回结果。

四步的职责分工：

| 步骤 | 做什么 | 是否调 LLM | 耗时 |
|------|--------|-----------|------|
| Rewrite | 把用户 query 中的指代/歧义/缺失补全 | ✅ 可能多次调 LLM | 1-5s |
| HistoryCache | 判断当前 query 能否复用历史 SQL | ✅ 调一次 | 1-3s |
| ValueRewrite | 改写缓存 SQL 中的值参数 | ✅ 调一次 | 1-2s |
| CacheConfirm | 反问用户确认是否复用 | ❌ 仅 interrupt | 用户决定 |

**核心设计理念**：每一步都保留了"放过"的出口。如果某步不确定或失败，不会卡死，而是**安全降级到完整链路**。

---

## 2. 流水线总览图

```
用户输入: "查一下华西大区的销售额，和上次一样"

┌─────────────────────────────────────────────────────────────────────┐
│ Step 1: Rewrite 子图                                                 │
│                                                                     │
│  输入: "查一下华西大区的销售额，和上次一样"                              │
│          │                                                          │
│          ▼                                                          │
│  [问题检测] → 检测到"上次"是指代 → 需要改写                          │
│          │                                                          │
│          ▼                                                          │
│  [改写执行] → 利用会话历史补全 → "查询华西大区2026年7月的销售额"        │
│          │                                                          │
│          ▼                                                          │
│  [问题检测] → 无问题 → 放行                                         │
│          │                                                          │
│  输出: user_query = "查询华西大区2026年7月的销售额"                    │
│        rewritten_query = "查询华西大区2026年7月的销售额"               │
└──────────────────────┬──────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Step 2: HistoryCache 节点                                            │
│                                                                     │
│  输入: user_query = "查询华西大区2026年7月的销售额"                    │
│                                                                     │
│  1. 召回 session 历史 + 取 metric_definitions                        │
│  2. LLM 调 CACHE_CHECK_PROMPT 判断是否可复用                        │
│                                                                     │
│  输出: cache_hit = True                                             │
│        cached_sql = "SELECT SUM(amount) FROM sales                  │
│                       WHERE region='华西' AND month='2026-07'"       │
│        cache_source = "session_history"                             │
│        cache_confidence = 0.92                                      │
│        cached_historical_query = "华西大区销售额"                     │
└──────────────────────┬──────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Step 3: ValueRewrite 节点                                            │
│                                                                     │
│  输入: cached_sql + cached_historical_query + user_query             │
│        (历史: "华西大区销售额" → 当前: "查询华西大区2026年7月的销售额")  │
│                                                                     │
│  LLM 调 VALUE_REWRITE_PROMPT 对比值参数差异                          │
│  发现: 历史 SQL 中 month='2026-07' 已匹配，无需改写                   │
│                                                                     │
│  输出: adjusted_cached_sql = "SELECT SUM(amount) FROM sales         │
│                                WHERE region='华西' AND month='2026-07'"│
│        (原样透传)                                                    │
└──────────────────────┬──────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Step 4: CacheConfirm 节点                                            │
│                                                                     │
│  interrupt(payload) → 挂起图，等用户回答                              │
│                                                                     │
│  显示: "检测到您曾查询过类似问题：                                      │
│         历史查询: 华西大区销售额                                       │
│         当前查询: 查询华西大区2026年7月的销售额                          │
│         是否复用以下 SQL?                                            │
│         SELECT SUM(amount) FROM sales WHERE ..."                     │
│                                                                     │
│  用户回答: "是" → cache_confirm_approved = True                       │
│                                                                     │
│  下游: run_single_query → execution 直接执行 cached_sql              │
│        (跳过 IR/SS/CG/Decision 全链路)                               │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. Step 1: Rewrite（LLM 改写子图）

### 3.1 位置

**在主图中的位置**：`pre_reject` 之后、`history_cache` 之前。

```
START → pre_reject → rewrite → history_cache → ...
```

### 3.2 做了什么

Rewrite 子图负责把用户输入的**不完整表达**改写为**完整语义的查询**。它解决三类问题：

| 问题类型 | 示例 | 改写后 |
|---------|------|--------|
| 指代（Anaphora） | "那去年的呢" | "查询去年的销售额" |
| 歧义（Ambiguity） | "查苹果的销量" | "查询苹果公司的产品销量" |
| 缺失（Missing） | "查销售额" | "查询所有门店的销售额" |

### 3.3 与下游的衔接

Rewrite 子图改写后的 query 写入 `user_query` 字段（覆盖原始输入）：

```python
# rewrite_subgraph.py — 适配器
def rewrite_node(state):
    rewritten = subgraph.invoke({
        "user_query": state["user_query"],
        "conversation_history": state.get("conversation_history", [])
    })
    return {
        "user_query": rewritten["user_query"],       # ← 改写后的 query 覆盖原字段
        "rewritten_query": rewritten["rewritten_query"],
        "rewrite_reason": rewritten["rewrite_reason"],
        "rewrite_round": rewritten["rewrite_round"],
    }
```

> **关键衔接点**：下游 `history_cache` 节点读的是 `state["user_query"]`——即改写后的 query。所以历史缓存检测是在**改写后的完整语义**上做的，而不是原始的用户输入。

### 3.4 改写失败的处理

- 如果改写子图检测到问题但无法解决（2 轮改写 + 反问后仍失败），会走 `output_degraded` 路径，输出原始 query 不变
- 此时 `history_cache` 仍然会用原始 query 尝试匹配——但匹配成功率会低很多

---

## 4. Step 2: HistoryCache（缓存命中检测）

### 4.1 位置

**在主图中的位置**：`rewrite` 之后、`value_rewrite` 之前。

```
rewrite → history_cache → route_after_cache
                                │
                    ┌───────────┴───────────┐
                    │ cache_hit=True        │ cache_hit=False
                    ▼                       ▼
             value_rewrite           task_planner（走完整链路）
```

### 4.2 输入来源

HistoryCache 的输入来自两条路径：

| 输入 | 来源 | 说明 |
|------|------|------|
| `user_query` | **Rewrite 子图改写后的 query** | 改写后的完整语义查询 |
| `conversation_history` | LangGraph checkpointer | 最近的对话轮次 |
| `metric_definitions` | UserMemory（API 层注入） | 跨 session 的指标定义 |
| `session_id` / `user_id` / `db_id` | API 入参 | 召回过滤条件 |

### 4.3 LLM 在此步的作用

HistoryCache 调 LLM 一次，使用 `CACHE_CHECK_PROMPT`。LLM 的任务是：

1. 对比当前 query 与历史轮次/指标定义
2. 判断是否意图等价（可复用）或仅值参数变化（仍可复用，交给 value_rewrite）
3. 输出 `{can_reuse, source, cached_sql, confidence, matched_turn_index}`

**LLM 调用参数**：
- `thinking=False`（不需要思考链，规则明确）
- `as_json=True`（强制 JSON 输出）
- `run_name="cache-check"`

### 4.4 输出与下游衔接

```python
# history_cache 节点输出到 state
{
    "cache_hit": True,                    # 下游路由依据
    "cached_sql": "SELECT ...",           # 复用的 SQL
    "cache_source": "session_history",    # 命中来源
    "cache_confidence": 0.92,             # 置信度
    "cached_historical_query": "...",     # 供 value_rewrite 比对
    "historical_sql_refs": [...],         # 未命中的参考也保留
}
```

> **关键衔接点**：`cache_hit=True` 时路由到 `value_rewrite`；`cached_historical_query` 是 value_rewrite 判断"值参数是否变化"的依据。如果 source 是 `metric_definition`，则该字段为空，value_rewrite 会降级为透传。

### 4.5 路由逻辑

```python
# main_graph.py
def route_after_cache(state: NL2SQLState) -> str:
    if state.get("cache_hit", False):
        return "value_rewrite"           # 走缓存加速链路
    return "task_planner"                # 走完整链路

# 边定义：history_cache → value_rewrite → cache_confirm → run_single_query
```

---

## 5. Step 3: ValueRewrite（值参数改写）

### 5.1 位置

**在主图中的位置**：`history_cache` 之后、`cache_confirm` 之前。

```
history_cache → value_rewrite → cache_confirm
```

### 5.2 为什么需要这一步

用户的两次查询可能"意图相同，仅值参数不同"。例如：

| 轮次 | 用户查询 | SQL |
|------|---------|-----|
| 历史 | "华东的销售额" | `WHERE region='华东'` |
| 当前 | "华西的销售额" | 需要改写为 `WHERE region='华西'` |

HistoryCache 的 `CACHE_CHECK_PROMPT` 规则 3 明确允许这种情况复用——值差异交由 value_rewrite 处理。

### 5.3 输入输出

```python
# 输入
cached_sql = "SELECT SUM(amount) FROM sales WHERE region='华东' AND month='2026-07'"
cached_historical_query = "华东的销售额"
user_query = "华西的销售额"

# LLM (VALUE_REWRITE_PROMPT) 对比后改写
adjusted_cached_sql = "SELECT SUM(amount) FROM sales WHERE region='华西' AND month='2026-07'"
```

### 5.4 LLM 在此步的作用

ValueRewrite 调 LLM 一次，使用 `VALUE_REWRITE_PROMPT`。LLM 的任务是：

1. 对比 `cached_historical_query` 和当前 `user_query`，找出值参数差异
2. 在 `cached_sql` 中找到对应位置，把旧值改写成新值
3. **绝不改动 SQL 结构**（表名、字段、JOIN、GROUP BY、聚合函数等）
4. **绝不增删 WHERE 谓词**
5. 不确定则原样返回 `cached_sql`

**LLM 调用参数**：
- `thinking=False`
- `as_json=True`
- `run_name="value-rewrite"`

### 5.5 三种降级路径

```python
def make_value_rewrite_node(...):
    if not cached_sql:
        return {"adjusted_cached_sql": None}        # 降级1: 无 SQL 可改写
    if not historical_query:
        return {"adjusted_cached_sql": cached_sql}  # 降级2: 无历史 query 对比，透传
    if not llm_client:
        return {"adjusted_cached_sql": cached_sql}  # 降级3: 无 LLM，透传
    # 正常调 LLM ...
    if exception:
        return {"adjusted_cached_sql": cached_sql}  # 异常降级: 透传
```

> **metric_definition 命中时的特例**：`cached_historical_query` 为空（指标定义没有"原始 query"概念），走降级 2，`adjusted_cached_sql = cached_sql`（指标 sql_pattern 原样透传）。

### 5.6 输出与下游衔接

```python
{
    "adjusted_cached_sql": "SELECT SUM(amount) FROM sales WHERE region='华西' AND month='2026-07'"
}
```

> **关键衔接点**：下游 `cache_confirm` 的确认文本中会显示"是否已改写值参数"的提示。下游 `execution` 优先用 `adjusted_cached_sql`，没有则用原 `cached_sql`。

---

## 6. Step 4: CacheConfirm（反问确认）

### 6.1 位置

**在主图中的位置**：`value_rewrite` 之后、`run_single_query` 之前。

```
value_rewrite → cache_confirm → run_single_query
                                │
                    ┌───────────┴───────────┐
                    │  approved=True        │  approved=False
                    ▼                       ▼
              execution 直接执行       cache_hit=False
              跳过 IR/SS/CG           走完整链路
```

### 6.2 做了什么

CacheConfirm 通过 `interrupt()` 暂停图执行，向用户推送确认信息，等待用户选择"复用"或"重新生成"。

**确认文本的构造**（`main_graph.py`）：

```
检测到您曾查询过类似问题：
历史查询: <cached_historical_query>
当前查询: <user_query>
[值参数已自动改写]
是否复用以下 SQL？
<SQL 内容（最多 5 行 / 200 字符，超出截断）>
```

### 6.3 不调 LLM

CacheConfirm **不调 LLM**。它只是构造确认文本并 `interrupt`。这是流水线中唯一没有 LLM 调用的步骤。

### 6.4 三种出口

| 场景 | 行为 | 下游 |
|------|------|------|
| **用户确认**（"是"） | `cache_confirm_approved=True` | execution 用 cached_sql 直接执行 |
| **用户否定**（"否"） | `cache_hit=False` + `cached_sql=None` | 走完整 IR/SS/CG 链路 |
| **interrupt 不可用**（测试环境） | 自动视为 approved | 同用户确认 |

### 6.5 否定回退的衔接细节

当用户否定时，`cache_confirm` 节点做了两件事：

```python
# main_graph.py — cache_confirm 否定分支
return {
    "cache_hit": False,           # 1. 恢复 cache_hit 为 False
    "cached_sql": None,           # 2. 清空缓存 SQL
    "cache_confirm_approved": False,
}
```

下游 `run_single_query` 的 `single_query_graph` 中，`route_start` 检测到 `cache_hit=False`，路由到 `"ir"` 分支——**重新跑完整链路**。

> 注意：`historical_sql_refs`（history_cache 阶段保留的弱参考）不会被清空，它们会作为 few-shot 候选喂给 CG 阶段。所以"否定回退"不是完全浪费——历史 SQL 仍然以参考形式参与生成。

---

## 7. 状态字段完整流转

以下展示关键状态字段在这四步中的完整生命周期：

```
                              user_query      cached_sql      cache_hit    adjusted_     cache_confirm_
                                                                           cached_sql    approved
                              ──────────      ──────────      ────────    ───────────    ─────────────
START                         原始输入         None            False       None           None
  │
  ▼
pre_reject                    原始输入         None            False       None           None
  │
  ▼
rewrite                       改写后           None            False       None           None
  │
  ▼
history_cache                 改写后           SQL 文本         True/       None           None
                                              (命中时)         False
  │
  ├─ cache_hit=True ──────────┤
  ▼
value_rewrite                 改写后           不变             True        改写后/透传    None
  │
  ▼
cache_confirm                 改写后           不变             True        (同上)         True/False
  │
  ├─ approved=True ───────────┤
  ▼
run_single_query              改写后           不变             True        (同上)         True
  │
  ▼
execution                     改写后           不变             True        (优先使用)     True
              (直接执行 adjusted_cached_sql 或 cached_sql，跳过 IR/SS/CG)
```

```
  ├─ approved=False ──────────┤
  ▼
run_single_query              改写后           None             False       None           False
  │
  ▼
ir                            改写后           None             False       None           False
  (走完整链路，historical_sql_refs 作为 CG 弱参考)
```

---

## 8. 反常路径分析

### 8.1 路径 A：正常命中（最理想）

```
rewrite → history_cache(hit) → value_rewrite(透传或改写) → cache_confirm(approved) → execution
```

**耗时**：约 5-10s（Rewrite 1-5s + HistoryCache 1-3s + ValueRewrite 1-2s + 人工确认 + Execution）
**收益**：跳过 IR/SS/CG/Decision 的 10-30s

### 8.2 路径 B：命中但否定

```
rewrite → history_cache(hit) → value_rewrite → cache_confirm(denied) → ir → ss → cg → execution → decision
```

**说明**：用户觉得缓存的 SQL 不适用，系统回退到完整链路。`historical_sql_refs` 作为 CG 的 few-shot 参考，不算完全浪费。

### 8.3 路径 C：改写后命中失败

```
rewrite → history_cache(miss) → task_planner → ir → ss → cg → ...
```

**说明**：Rewrite 改写了 query，但改写后的 query 仍然没有匹配到历史 SQL。正常走完整链路。

### 8.4 路径 D：改写失败 → 缓存也不命中

```
rewrite(输出原始 query) → history_cache(miss) → task_planner → ...
```

**说明**：Rewrite 无法解决指代/歧义，输出原始 query。HistoryCache 用原始 query 匹配，大概率也不命中。走完整链路。

### 8.5 路径 E：metric_definition 命中

```
rewrite → history_cache(hit, source=metric_definition) → value_rewrite(降级透传) → cache_confirm → execution
```

**说明**：命中的是跨 session 的指标定义。`cached_historical_query` 为空 → value_rewrite 降级透传原 SQL。
**典型场景**：用户 A 会话教过"活跃用户数 = ..."，用户 A 在 B 会话问"活跃用户数"时命中。

### 8.6 路径 F：Rewrite 反问中

```
rewrite(detect_issues → rewrite_execute → detect_issues → clarify → interrupt)
  ↑ (用户回答后 resume)
rewrite(rewrite_execute → detect_issues → pass) → history_cache → ...
```

**说明**：Rewrite 子图在 2 轮改写后仍有问题，触发 `clarify` 节点 `interrupt` 反问用户。此时 graph 挂起，**尚未进入 history_cache**。用户补充信息后 resume，继续改写循环，通过后才进入 history_cache。

---

## 9. 关键文件索引

| 文件 | 作用 | 涉及步骤 |
|------|------|---------|
| `src/rewrite/rewrite_subgraph.py` | Rewrite 子图编排 | Step 1 |
| `src/rewrite/pre_reject.py` | 前置拒答检测 | Step 1 前置 |
| `src/rewrite/prompts.py` | DETECT_ISSUES_PROMPT / REWRITE_EXECUTE_PROMPT | Step 1 |
| `src/memory/history_cache.py` | HistoryCache 类 + 安全边界 | Step 2 |
| `src/memory/session_recall.py` | HybridSessionRetriever（Dense+BM25+RRF） | Step 2 子步骤 |
| `src/memory/prompts.py:30-59` | CACHE_CHECK_PROMPT | Step 2 |
| `src/memory/prompts.py:62-88` | VALUE_REWRITE_PROMPT | Step 3 |
| `src/graph/main_graph.py:137-197` | make_history_cache_node 节点工厂 | Step 2 |
| `src/graph/main_graph.py:200-273` | make_value_rewrite_node 值改写节点 | Step 3 |
| `src/graph/main_graph.py:276-366` | make_cache_confirm_node 反问确认节点 | Step 4 |
| `src/graph/main_graph.py:1208-1228` | 边定义 + route_after_cache 路由 | 衔接逻辑 |
| `src/graph/state.py` | NL2SQLState 字段定义 | 状态流转 |
| `src/graph/single_query_graph.py:76-84` | route_start: cache_hit 路由 | Step 4 下游 |
| `src/api/routes/query.py` | SSE 流 + interrupt 处理 | Step 4 中断/恢复 |
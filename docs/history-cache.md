# HistoryCache 节点详解

> 版本: 0.1.0 · 更新日期: 2026-07-12
>
> 本文件专门讲解主图中 `history_cache` 节点及其下游链路（`value_rewrite` -> `cache_confirm` -> `execution`）的完整逻辑。面向读者：希望理解"为什么新会话也会命中缓存""缓存命中后 SQL 怎么流到执行"的开发者。配套阅读：[nl2sql-workflow.md](./nl2sql-workflow.md) 第 2 节主图流程。

---

## 目录

1. [定位与设计意图](#1-定位与设计意图)
2. [在主图中的位置](#2-在主图中的位置)
3. [节点输入](#3-节点输入)
4. [执行步骤详解](#4-执行步骤详解)
5. [两种命中来源](#5-两种命中来源session_history-vs-metric_definition)
6. [命中后的下游链路](#6-命中后的下游链路)
7. [未命中的走向](#7-未命中的走向)
8. [安全与降级设计](#8-安全与降级设计)
9. [关键文件索引](#9-关键文件索引)

---

## 1. 定位与设计意图

`history_cache` 是主图的**第一个节点**（`START -> history_cache`，`main_graph.py:1209`）。它在 IR（信息召回）之前判断：当前查询是否可以**直接复用一段已知 SQL**，从而跳过昂贵的 IR / SS / CG 链路。

三条设计底线（`history_cache.py` 文件头注释）：

| 底线 | 含义 | 体现位置 |
|------|------|---------|
| `confidence < 0.8` 不复用 | 低置信度命中走完整链路 | `HistoryCache.check` 安全边界 |
| 涉及时间变化的 follow-up 不复用 | 数据可能已变，复用旧 SQL 会过期 | `CACHE_CHECK_PROMPT` 规则 4 |
| 只复用 SQL，不复用 result | 重新执行保证时效性 | `execution` 节点用 cached_sql 重新跑 |

> **核心思想**：复用"结构"，不复用"数据"。命中的 SQL 模板会被重新执行一遍，拿到的是当前数据库的最新结果。

---

## 2. 在主图中的位置

```
START
  │
  ▼
┌─────────────────────────────────────────────────────────────┐
│                    history_cache 节点                        │
│   召回 session 历史 + 取 metric_definitions                  │
│   ──────────────────────────────────────────                │
│   LLM 判断 (CACHE_CHECK_PROMPT) -> CacheResult              │
│   安全边界过滤 (confidence≥0.8 + 有 cached_sql)              │
└──────────────────────────┬──────────────────────────────────┘
                           │
                  route_after_cache
                           │
            ┌──────────────┴───────────────┐
            │ cache_hit=True               │ cache_hit=False
            ▼                              ▼
     ┌────────────┐                 ┌──────────────┐
     │value_rewrite│                 │ task_planner │ ── 走完整 IR/SS/CG 链路
     └──────┬─────┘                 └──────────────┘
            ▼
     ┌────────────┐
     │cache_confirm│  interrupt 反问"是否复用"
     └──────┬─────┘
            │  用户确认 / 否定(否定则 cache_hit=False 回退)
            ▼
     ┌──────────────┐
     │ run_single_query │ ──> execution 用 cached_sql 直接构造候选
     └──────────────┘      (跳过 IR/SS/CG)
```

对应代码：`main_graph.py:1214-1228`

```python
def route_after_cache(state: NL2SQLState) -> str:
    if state.get("cache_hit", False):
        return "value_rewrite"
    return "task_planner"

# value_rewrite -> cache_confirm -> run_single_query
```

---

## 3. 节点输入

`history_cache` 节点（`make_history_cache_node`，`main_graph.py:137-197`）从 state 取以下字段：

| State 字段 | 来源 | 用途 |
|-----------|------|------|
| `user_query` | API 入参 | 当前查询，送给 LLM 判断 |
| `user_id` | API 入参 | session 历史召回过滤 |
| `database_filter` | API 入参（db_id） | session 历史召回过滤 |
| `conversation_history` | LangGraph checkpointer（`thread_id=session_id`） | **兜底**：当 session 召回为空时作为候选 |
| `metric_definitions` | `query.py:158` `user_memory.get_metric_definitions(min_confidence=0.7)` | **长期记忆**，跨会话，作为命中候选之一 |
| `session_id` | `session_memory` ctx（`getattr(session_memory, "session_id", "")`） | session 历史召回过滤 |

> 注意 `session_id` 不是从 state 取，而是从 `session_memory` 上下文对象取（`main_graph.py:153`）。这是决策 12 的体现：`_user_memory` / `_session_memory` 是 Python 对象实例，不能放进 state，由 `run_graph` 在 `current_session_memory.set(...)` 后通过 ctx 暴露给节点。

---

## 4. 执行步骤详解

### 4.1 禁用判断

```python
if history_cache is None:
    return {"cache_hit": False, ...}  # 禁用，直接走 task_planner
```

`history_cache` 实例由 `build_main_graph(..., history_cache=...)` 传入（`main_graph.py:1141`），None 时整条缓存链路跳过。

### 4.2 Session 历史召回

```python
recalled_refs = history_cache.recall_session_history(
    state["user_query"], user_id=user_id, session_id=session_id, db_id=db_id,
)
```

仅当 `user_id and session_id and db_id` 三者齐全且 `history_cache` 暴露了 `recall_session_history` 方法时才召回（`main_graph.py:156`）。

召回由 `HybridSessionRetriever.retrieve`（`session_recall.py:485`）执行，**三路混合**：

```
                     user_query
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
     ┌─────────┐   ┌──────────┐   (无第三路，两路融合)
     │  Dense  │   │   BM25   │
     │ Chroma  │   │ 本地实现 │
     └────┬────┘   └────┬─────┘
          │              │
          │   过滤：user_id + session_id + db_id + success=True
          │   （Chroma where / JSON list_turns 都严格按 session_id）
          └──────┬───────┘
                 ▼
          ┌────────────┐
          │  RRF 融合   │  score = 1/(k+dense_rank) + 1/(k+bm25_rank)
          │  k=60       │  threshold=0.015
          └──────┬─────┘
                 ▼
          load_turn_window 回表补全 query/sql
                 │
                 ▼
        List[HistoricalSQLReference]
```

**关键隔离边界**：Dense（`session_recall.py:186-193`）和 BM25（`session_recall.py:342`）都**强制按 `session_id` 过滤**。这意味着：

> **新会话（新 session_id）的 session 历史召回结果必为空。**

任何一路异常都安全降级为空（`session_recall.py:502-504, 514-516`），不会阻断主流程。

### 4.3 兜底：conversation_history

```python
recalled_history = [ref.to_turn() for ref in recalled_refs]
check_history = recalled_history or conversation_history  # main_graph.py:165
```

- 优先用 session 召回结果（带 RRF 分数、回表补全的高质量候选）
- 召回为空时（典型场景：新会话），**fallback 到 `conversation_history`**——这是 LangGraph checkpointer 按 `thread_id=session_id` 存的对话历史
- 新会话里 `conversation_history` 同样为空，所以新会话的 `check_history` 最终为空

### 4.4 LLM 命中检测

`HistoryCache.check`（`history_cache.py:60-108`）：

1. **短路**：`session_history` 和 `metric_definitions` 都为空 -> 直接返回 `CacheResult(hit=False)`，不调 LLM（`history_cache.py:77-78`）
2. **格式化**：history 取最近 5 轮（`_format_history`，`history_cache.py:113`），metrics 格式化为 `name(desc): sql_pattern`
3. **调用 LLM**：`CACHE_CHECK_PROMPT`，`invoke(as_json=True, thinking=False, run_name="cache-check")`——准实时场景，不走 stream、不要思考链（`history_cache.py:94`）

`CACHE_CHECK_PROMPT`（`prompts.py:30-59`）的判断规则：

| 规则 | 判断 | 是否复用 |
|------|------|---------|
| 1 | 与历史某轮意图等价（意图相同、参数相同） | 复用该轮 SQL |
| 2 | 可用已知指标定义直接回答 | 用指标定义的 sql_pattern |
| 3 | 仅值参数变化（WHERE 值/地区/产品/阈值/LIMIT/HAVING） | 仍复用，值差异交由 value_rewrite |
| 4 | follow-up 但意图/结构变化（增删 WHERE/GROUP BY/ORDER BY/聚合/表/JOIN） | **不复用** |
| 5 | confidence < 0.8 | 返回 false |

LLM 输出 JSON：

```json
{
  "can_reuse": true,
  "source": "session_history | metric_definition | null",
  "cached_sql": "复用的 SQL",
  "confidence": 0.0-1.0,
  "matched_turn_index": "轮次索引或 null",
  "reason": "判断理由"
}
```

### 4.5 响应解析与历史 query 回填

`_parse_response`（`history_cache.py:132-175`）解析 JSON，并回填 `historical_query`（命中的原始自然语言查询，供下游 `value_rewrite` 比对值参数用）：

```
hit && source=="session_history"
         │
         ├─ 优先：matched_turn_index 匹配 turn_index/turn_id
         │        （兼容 int/str，main_graph.py:152-157）
         │
         └─ 兜底：按 cached_sql 精确反查
                  norm(s) = strip + rstrip(";") + strip
                  （忽略首尾空白与末尾分号差异，history_cache.py:158-165）
```

> 注：`metric_definition` source 不回填 `historical_query`（指标定义没有"原始 query"概念），下游 `value_rewrite` 会因 `historical_query` 为空而走透传降级。

### 4.6 安全边界

`HistoryCache.check` 在 LLM 返回后做三道闸门（`history_cache.py:101-106`）：

```python
if not result.hit:                              return CacheResult(hit=False)
if result.confidence < self.min_confidence:     return CacheResult(hit=False)  # 默认 0.8
if not result.cached_sql:                       return CacheResult(hit=False)
```

任一不满足即降级为不命中，走完整链路。

### 4.7 输出与事件

节点输出写入 state（`main_graph.py:176-195`）：

| 输出字段 | 含义 |
|---------|------|
| `cache_hit` | 是否命中 |
| `cached_sql` | 命中的 SQL（来自历史轮次或指标 sql_pattern） |
| `cache_source` | `session_history` / `metric_definition` / None |
| `cache_confidence` | 置信度 |
| `cached_historical_query` | 命中的历史 query（供 value_rewrite） |
| `historical_sql_refs` | session 召回的弱参考列表，**命不命中都保留**，供否定回退时使用 |
| `trace_log` | 追加 `[HistoryCache] hit=..., source=..., confidence=..., recalled=N` |

同时 emit `cache_check` 业务事件（前端时间轴渲染"缓存命中"节点的数据源）：

```python
emit_safe("cache_check", {
    "hit": result.hit, "source": result.source,
    "confidence": result.confidence, "cached_sql": result.cached_sql,
    "recalled": len(recalled_refs), "historical_sql_refs": historical_sql_refs,
})
```

前端 reducer 将其渲染为摘要 `缓存命中 · {source} · conf={confidence}`（`frontend/src/store/reducer.ts:142`）。

---

## 5. 两种命中来源：session_history vs metric_definition

这是理解"新会话为什么也命中缓存"的关键。

```
                    HistoryCache.check()
                           │
           ┌───────────────┴───────────────┐
           ▼                               ▼
   source = session_history        source = metric_definition
           │                               │
   候选来源：                       候选来源：
   HybridSessionRetriever          user_memory.get_metric_definitions(
   (Dense + BM25 + RRF)              min_confidence=0.7)
           │                               │
   存储：                           存储：
   Chroma (nl2sql_session_queries)  data/user_memory/{user_id}.json
   + JSON conversation store        六维记忆之 metric_definitions 维
           │                               │
   隔离边界：                       隔离边界：
   user_id + session_id + db_id    仅 user_id
   + success=True                  （跨所有会话持久化）
           │                               │
   ┌───────┴───────┐                新会话也会有候选 ✓
   │               │                -> 这是「长期记忆」
   新会话=空 ✓     旧会话才有
   -> 正常         -> 命中正常
                   -> 若新会话命中=bug
                      (session_id 未隔离)
```

### 5.1 session_history（会话内历史）

- **生命周期**：随 session 存在。每次成功查询由 `memory_update` 节点写入召回库（`SessionQueryMemory`，`session_recall.py:42`）
- **隔离**：Dense 和 BM25 双路都强制 `session_id` 过滤，新会话召回必为空
- **典型命中**：同一会话内重复问相似问题、或仅值参数变化（"华东的销售额" -> "华西的销售额"）

### 5.2 metric_definition（长期记忆指标定义）

- **生命周期**：随 user 持久化。`memory_update` 节点用 `METRIC_EXTRACT_PROMPT`（`prompts.py:12`）从成功 SQL 提取指标定义，写入 `user_memory.metric_definitions`（auto_learned + user_taught 双轨）
- **隔离**：仅按 `user_id`，**跨所有会话**
- **加载入口**：`query.py:158` `initial_state["metric_definitions"] = user_memory.get_metric_definitions(min_confidence=0.7)`——每次请求都按 user_id 重新加载，新会话也不例外
- **典型命中**：用户在 A 会话教过"活跃用户数 = ..."，B 会话问"活跃用户数"时命中

### 5.3 判断"新会话命中"是长期记忆还是 bug

看前端时间轴 cache 节点摘要的 `source` 字段，或后端 trace_log：

```
[HistoryCache] hit=True, source=metric_definition, confidence=0.92, recalled=0
```

| 观察到的 source | recalled | 结论 |
|----------------|----------|------|
| `metric_definition` | 0 | **长期记忆，设计如此，非 bug** |
| `session_history` | 0 | 可疑：session_history 候选为空却命中，可能是 conversation_history 兜底命中或 LLM 幻觉 |
| `session_history` | >0 | **bug 嫌疑**：新会话不该有 session 召回，说明 session_id 未真正切换 |

> `recalled` 是当前 session 召回到的历史条数。新会话应为 0。

---

## 6. 命中后的下游链路

### 6.1 value_rewrite（值参数改写）

`make_value_rewrite_node`（`main_graph.py:200-273`）。仅在 `cache_hit=True` 时进入。

**目的**：当用户仅改了值参数（如时间、地区、阈值），把 `cached_sql` 里的旧值改写成当前查询的新值。

```
输入：cached_sql + cached_historical_query + user_query
  │
  ├─ 降级1：无 cached_sql          -> adjusted_cached_sql = None
  ├─ 降级2：无 historical_query    -> adjusted_cached_sql = cached_sql（透传）
  ├─ 降级3：无 llm_client          -> adjusted_cached_sql = cached_sql（透传）
  │
  └─ LLM (VALUE_REWRITE_PROMPT)   -> adjusted_sql, changed, reason
                                       异常 -> 透传原 cached_sql
```

`VALUE_REWRITE_PROMPT`（`prompts.py:62-88`）的硬约束：
- 仅改写**已存在的值参数**（WHERE 值 / LIMIT / HAVING 值等）
- 绝不改动 SQL 结构（表名、字段、聚合、GROUP BY/ORDER BY/JOIN）
- 绝不增删 WHERE 谓词
- 不确定则原样返回

> **metric_definition 命中时**：`cached_historical_query` 为空 -> 走降级 2 -> `adjusted_cached_sql = cached_sql`（指标 sql_pattern 原样透传，不做值改写）。

emit `value_rewrite` 事件（`main_graph.py:263`），前端可展示"已自动改写值参数"。

### 6.2 cache_confirm（反问确认）

`make_cache_confirm_node`（`main_graph.py:276-366`）。通过 `interrupt()` 暂停图，向用户确认是否复用。

```
                  cache_confirm 节点
                        │
        ┌───────────────┼───────────────────┐
        │               │                   │
  测试逃逸          正常反问           interrupt 不可用
  (预置 approved)   interrupt(payload)  (向后兼容)
  直接使用          暂停等用户答        自动复用
        │               │                   │
        │               ▼                   │
        │       解析用户选择                │
        │       {"复用","reuse","yes",      │
        │        "是","1","y","确认","Y"}   │
        │               │                   │
        │       ┌───────┴───────┐           │
        │       ▼               ▼           │
        │    approved=True   approved=False  │
        │       │               │           │
        └───────┴───────┬───────┴───────────┘
                        ▼
              cache_confirm_approved
              (False 时同时置 cache_hit=False,
               清空 cached_sql -> 回退到完整链路)
```

- **确认文本**：以意图为主、SQL 为辅。SQL 超过 5 行或 200 字符会截断（`main_graph.py:307-312`）
- **测试逃逸**：`state.cache_confirm_approved` 预置非 None 时跳过 interrupt（`main_graph.py:293-302`），便于单测
- **用户否定**：`cache_hit=False` + `cached_sql=None`（`main_graph.py:353-356`）。后续 `run_single_query -> single_query_graph`，其入口条件边 `route_start`（`single_query_graph.py:76-84`）检测到 `cache_hit=False` -> 走 `"ir"` 分支，**重新跑完整 IR/SS/Answerability/CG/Execution/Decision 链路**。`historical_sql_refs`（history_cache 节点保留的 session 召回弱参考）会作为 few-shot 候选喂给 CG 参考。否定回退因此是干净的：用户说“不”，系统就当没命中过，从头生成。

emit `cache_confirm` 事件（`main_graph.py:358`）。

### 6.3 execution（直接执行，跳过 IR/SS/CG）

`cache_confirm -> run_single_query`（`main_graph.py:1228`）。`run_single_query` 调用 `single_query_graph`，其内部 `execution` 节点（`main_graph.py:913-964`）检测到 `cache_hit=True` 时：

```python
if state.get("cache_hit", False):
    cached_sql = state.get("adjusted_cached_sql") or state.get("cached_sql", "")
    cand = SQLCandidate(id="cache_hit", sql=cached_sql, status=SQLStatus.PENDING)
    candidates = [cand]   # 唯一候选，跳过 IR/SS/CG
```

- 优先用 `adjusted_cached_sql`（经值改写的），否则用原 `cached_sql`
- 构造唯一候选 `id="cache_hit"`，一次性执行（决策 51，不在执行阶段修复）
- 重新执行拿到当前数据库的最新结果——这就是"只复用 SQL，不复用 result"的体现

---

## 7. 未命中的走向

`cache_hit=False` -> `route_after_cache` 返回 `"task_planner"`（`main_graph.py:1215-1217`）-> 走完整链路：

```
task_planner ─┬─ REJECT ─────────────────────> END（拒答）
              ├─ EXECUTE single ─> run_single_query ─> memory_update ─> END
              └─ EXECUTE multi  ─> run_subqueries ─> aggregate_results ─> memory_update ─> END
```

`task_planner` 之后的 IR / SS / Answerability / CG / Execution / Decision 全套正常运转，`historical_sql_refs`（history_cache 节点保留的 session 召回弱参考）会作为 few-shot 候选喂给 CG 阶段参考——即使没命中缓存，召回的历史 SQL 也不浪费。

---

## 8. 安全与降级设计

`history_cache` 链路采用**多层安全降级**，任一环节失败都不阻断主流程：

| 环节 | 失败情况 | 降级行为 | 代码位置 |
|------|---------|---------|---------|
| session 召回 | Dense 异常 | 返回空，继续 BM25 | `session_recall.py:502` |
| session 召回 | BM25 异常 | 返回空，用 Dense | `session_recall.py:514` |
| session 召回 | 回表失败 | 保留索引元数据 | `session_recall.py:535` |
| recall_session_history | 任何异常 | 返回空列表 | `history_cache.py:58` |
| LLM 命中检测 | 任何异常 | `CacheResult(hit=False)` | `history_cache.py:96` |
| 安全边界 | confidence<0.8 / 无 sql | 降级为不命中 | `history_cache.py:101-106` |
| value_rewrite | 无 cached_sql/historical_query/llm/异常 | 透传或 None | `main_graph.py:218-256` |
| cache_confirm | interrupt 不可用 | 自动复用 | `main_graph.py:332-340` |
| cache_confirm | 用户否定 | cache_hit=False 回退完整链路 | `main_graph.py:353-356` |

**设计哲学**：缓存是加速器，不是必经路。任何不确定都倾向"不复用、走完整链路"，用正确性换性能。

---

## 9. 关键文件索引

| 文件 | 作用 |
|------|------|
| `src/graph/main_graph.py:137-197` | `make_history_cache_node` 节点工厂 |
| `src/graph/main_graph.py:200-273` | `make_value_rewrite_node` 值改写节点 |
| `src/graph/main_graph.py:276-366` | `make_cache_confirm_node` 反问确认节点 |
| `src/graph/main_graph.py:913-964` | `make_execution_node` 命中时直接执行 |
| `src/graph/main_graph.py:1208-1228` | 边定义：入口 + route_after_cache + 命中链路 |
| `src/memory/history_cache.py` | `HistoryCache` 类 + `CacheResult` + 安全边界 |
| `src/memory/session_recall.py` | `HybridSessionRetriever`（Dense+BM25+RRF）+ 严格 session_id 过滤 |
| `src/memory/user_memory.py` | `UserMemory` 长期记忆，`get_metric_definitions` |
| `src/memory/prompts.py:30-59` | `CACHE_CHECK_PROMPT` |
| `src/memory/prompts.py:62-88` | `VALUE_REWRITE_PROMPT` |
| `src/graph/state.py:142-152` | state 字段定义（cache_hit / cached_sql / cache_source 等） |
| `src/api/routes/query.py:158` | `metric_definitions` 注入入口 |
| `frontend/src/store/reducer.ts:142` | 前端 cache_check 事件处理，渲染 `缓存命中 · {source}` |

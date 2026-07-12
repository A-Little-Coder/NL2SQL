# NL2SQL 记忆系统（Memory System）文档

> 本文档全面梳理 NL2SQL 项目中的记忆体系，涵盖 **Claude Code 项目记忆**（框架层）和 **NL2SQL 应用记忆**（业务层）的生成、存储、读取和使用机制。

---

## 目录

1. [记忆系统总览](#1-记忆系统总览)
2. [Claude Code 项目记忆（框架层）](#2-claude-code-项目记忆框架层)
3. [NL2SQL 应用记忆（业务层）](#3-nl2sql-应用记忆业务层)
   - 3.1 [UserMemory——用户长期记忆](#31-usermemory用户长期记忆)
   - 3.2 [SessionMemory——会话记忆](#32-sessionmemory会话记忆)
   - 3.3 [SessionMemory v2——混合召回系统](#33-sessionmemory-v2混合召回系统)
   - 3.4 [底层存储 Storage](#34-底层存储-storage)
4. [Memory 生成时机（触发链路）](#4-memory-生成时机触发链路)
   - 4.1 [MemoryUpdater 自动学习](#41-memoryupdater-自动学习)
   - 4.2 [会话历史写入](#42-会话历史写入)
5. [Memory 使用机制（加载到上下文）](#5-memory-使用机制加载到上下文)
   - 5.1 [加载到 Graph State](#51-加载到-graph-state)
   - 5.2 [ContextVar 跨节点传递](#52-contextvar-跨节点传递)
   - 5.3 [各节点如何使用记忆](#53-各节点如何使用记忆)
6. [HistoryCache 缓存命中检测](#6-historycache-缓存命中检测)
7. [前端 Memory 展示](#7-前端-memory-展示)
8. [关键配置项](#8-关键配置项)
9. [安全机制](#9-安全机制)
10. [测试覆盖](#10-测试覆盖)
11. [架构总图](#11-架构总图)

---

## 1. 记忆系统总览

NL2SQL 项目存在**两套独立的记忆系统**，运行在不同层面：

| 维度 | Claude Code 项目记忆 | NL2SQL 应用记忆 |
|------|-------------------|----------------|
| **所属层面** | Claude Code 开发框架层 | NL2SQL 应用业务层 |
| **存储位置** | `C:\Users\WangHongZe\.claude\projects\...\memory\` | `./memory/`（由 `MEMORY_DIR` 配置） |
| **管理方式** | Claude Code 自动管理 | 应用代码自主管理 |
| **用途** | 跨会话保留项目上下文（架构、bug 记录、用户偏好） | 为 NL2SQL 查询提供用户画像、会话上下文、历史复用 |
| **文件格式** | Markdown + YAML front matter | JSON 文件 + Chroma 向量索引 |

---

## 2. Claude Code 项目记忆（框架层）

### 2.1 存储结构

```
memory/
  MEMORY.md                                  -- 记忆索引（Markdown wiki 风格）
  frontend-ui-change.md                      -- 前端工程架构记录
  backend-cache-confirm-emits-error.md       -- 后端 bug 记录（已修复）
```

### 2.2 文件格式规范

每个记忆文件包含 YAML front matter 和正文：

```markdown
---
name: <short-kebab-case-slug>
description: <单行摘要——用于判断相关性时检索>
metadata:
  type: user | feedback | project | reference
  node_type: memory
  originSessionId: <session-id>
---

<记忆正文内容>
```

- `type: user` —— 用户画像（角色、专长、偏好）
- `type: feedback` —— 用户给出的工作指导（含 Why 和 How to apply）
- `type: project` —— 项目持续目标、约束（非代码可推导的）
- `type: reference` —— 外部资源链接

### 2.3 索引机制

`MEMORY.md` 是索引文件，每行一个链接：

```markdown
- [前端工程 frontend-ui-change](frontend-ui-change.md) - NL2SQL 前台工程位置/架构/关键文件
- [后端 cache_confirm 双发事件 bug（已修复）](backend-cache-confirm-emits-error.md) - 缓存命中反问曾双发 clarification+error
```

- 每次会话启动时，Claude Code 自动读取 `MEMORY.md` 全文注入上下文
- 正文中可用 `[[name]]` 语法链接相关记忆（跨文件引用）
- 更新时直接覆写文件，旧内容丢失

### 2.4 生成时机

- **用户主动要求**记忆（`/remember` 或 "请记住……"）
- **Claude Code 自主判断**：在对话中识别出需要持久化的信息（用户偏好、项目决策、bug 信息）
- **写入前检查**：已有同名文件则更新，而非新建；避免写入代码/版本控制已记录的内容

---

## 3. NL2SQL 应用记忆（业务层）

### 3.1 UserMemory——用户长期记忆

**文件**：`src/memory/user_memory.py`
**存储**：`data/user_memory/{user_id}.json`
**生命周期**：跨会话持久化，用户删除才消失

#### 3.1.1 六维记忆结构

```python
{
    "user_id": "xxx",
    "created_at": "2025-01-01T00:00:00",
    "updated_at": "2025-01-02T00:00:00",
    "term_preferences": {},           # 术语偏好（用户澄清/主动教的映射）
    "frequently_used_tables": {},     # 常用表（自动学习）
    "metric_definitions": {},         # 指标定义（auto_learned + user_taught 双轨）
    "query_preferences": {},          # 查询偏好（默认排序/limit/分组粒度）
    "domain_context": {},             # 领域上下文（行业/部门/关注领域）
    "clarification_history": [],      # 反问澄清历史
}
```

#### 3.1.2 指标定义双轨制

```python
"metric_definitions": {
    "销售额": {
        "name": "销售额",
        "description": "订单总金额",
        "sql_pattern": "SUM(amount)",
        "source": "auto_learned",   # 自动学习来源
        "confidence": 0.85,
        "tables": ["orders"],
        "learned_at": "...",
        "updated_at": "...",
    },
    "同比增长率": {
        "name": "同比增长率",
        "description": "用户自定义的指标",
        "sql_pattern": "(current - previous) / previous",
        "source": "user_taught",    # 用户主动教导来源（不会被覆盖）
        "confidence": 1.0,
        "tables": ["sales"],
        "learned_at": "...",
        "updated_at": "...",
    }
}
```

- `auto_learned` 条目：由 MemoryUpdater 自动从 SQL 中提取
- `user_taught` 条目：用户在前端主动输入，不会被自动学习覆盖
- 读取时可通过 `min_confidence` 参数过滤低置信度指标

#### 3.1.3 API 暴露

- `GET /api/v1/users/{user_id}/memory` —— 返回完整六维记忆
- `GET /api/v1/users/{user_id}/metrics` —— 返回指标定义列表

---

### 3.2 SessionMemory——会话记忆

**文件**：`src/memory/session_memory.py`
**存储**：`data/sessions/{user_id}/{session_id}.json`
**生命周期**：单次会话内，由 SessionManager 管理

#### 3.2.1 数据结构

```python
{
    "session_id": "xxx",
    "user_id": "xxx",
    "created_at": "...",
    "updated_at": "...",
    "status": "active",
    "conversation_history": [
        {
            "turn_index": 1,
            "timestamp": "...",
            "user_query": "...",
            "final_sql": "...",
            "cache_hit": False,
            # ... 仅白名单字段
        }
    ],
    "context_summary": {
        "last_topic": "...",
        "last_tables": ["orders", "customers"],
        "last_time_range": "2025-01-01 ~ 2025-01-31"
    }
}
```

#### 3.2.2 白名单字段过滤

`_ALLOWED_TURN_FIELDS` 白名单：只允许写入以下字段，避免存储膨胀和序列化失败：

- `user_query` —— 用户查询
- `final_sql` —— 最终 SQL
- `cache_hit` —— 是否缓存命中
- `cache_source` —— 缓存来源
- `rejection_reason` —— 拒答原因
- `error` —— 错误信息
- `result_meta` —— 结果元信息
- `clarification_round` —— 反问轮次
- `final_result_sample` —— 结果样本（非全量）

`final_result` 大字段**不存储**。

#### 3.2.3 SessionManager 会话管理

**文件**：`src/memory/session_manager.py`
**功能**：会话生命周期管理

- `create_session(user_id, session_id)` —— 创建新会话
- `get_session(session_id)` —— 获取会话
- `get_or_create_session(session_id, user_id)` —— 获取或创建
- `list_sessions(user_id, page, page_size)` —— 列出用户会话
- `delete_session(session_id)` —— 删除会话
- `add_turn(session_id, turn_data)` —— 添加对话轮次
- `get_recent_turns(session_id, n)` —— 获取最近 N 轮对话

**缓存机制**：进程内 LRU 缓存，最大 200 个 SessionMemory 实例，减少磁盘 I/O。

---

### 3.3 SessionMemory v2——混合召回系统

**文件**：`src/memory/session_recall.py`
**目的**：支持在 HistoryCache 节点中召回当前 session 内的历史成功查询，供 LLM 判断是否复用。

#### 3.3.1 三路混合召回架构

```
user_query
    │
    ├─── Dense（Chroma 向量索引）
    │     存储：chroma/nl2sql_session_queries
    │     召回：向量相似度，按 user_id + session_id + db_id + success=True 过滤
    │
    ├─── BM25（本地轻量实现）
    │     数据源：JSON ConversationStore
    │     召回：词频统计，按 session_id 过滤
    │
    └─── RRF（Reciprocal Rank Fusion）
           score = 1/(k + dense_rank) + 1/(k + bm25_rank)
           默认 k=60，threshold=0.015
```

**隔离边界**：Dense 和 BM25 都强制按 `session_id` 过滤，新会话召回必为空。

#### 3.3.2 写入时机

`MemoryUpdater._update_session_recall_memory()` —— 成功查询（有 `final_sql`、无错误、无拒答、结果验证通过）后写入。

---

### 3.4 底层存储 Storage

**文件**：`src/memory/storage.py`
**职责**：底层 JSON 文件读写

#### 3.4.1 核心特性

| 特性 | 实现方式 |
|------|---------|
| **跨平台文件锁** | Windows 用 `msvcrt.locking()`，Unix 用 `fcntl.flock()` |
| **原子写入** | 先写 `.tmp` 临时文件 → `fsync` 落盘 → `os.replace()` 原子替换 |
| **锁超时** | 默认 5 秒，超时抛 `TimeoutError` |
| **读故障恢复** | 读取失败时尝试读取 `.tmp` 备份文件 |

#### 3.4.2 路径工具

- `user_path(user_id)` —— 用户记忆文件路径
- `session_path(user_id, session_id)` —— 会话记忆文件路径
- `list_user_session_files(user_id)` —— 列出用户所有会话文件

---

## 4. Memory 生成时机（触发链路）

### 4.1 MemoryUpdater 自动学习

**文件**：`src/memory/memory_updater.py`
**触发时机**：主图的 `memory_update` 节点，在每次 graph 执行完成后触发。

```
主图执行流程：
START → history_cache → (命中? value_rewrite → cache_confirm | 未命中? task_planner)
     → run_single_query（或 run_subqueries → aggregate_results）
     → memory_update → END
```

**`memory_update` 节点**（`src/graph/main_graph.py:371-389`）依次执行 6 个子步骤：

| 步骤 | 方法 | 触发条件 | 写入目标 |
|------|------|---------|---------|
| 1. 常用表 | `_update_table_usage` | 有 `final_sql` | UserMemory.frequently_used_tables |
| 2. 指标定义 | `_update_metric_definitions` | SQL 含聚合函数 | UserMemory.metric_definitions |
| 3. 查询偏好 | `_update_query_preferences` | SQL 含 ORDER/GROUP/LIMIT | UserMemory.query_preferences |
| 4. 会话上下文 | `_update_session_context` | 有 `user_query` | SessionMemory.context_summary |
| 5. 澄清历史 | `_update_clarification_history` | 有 clarification_history | UserMemory.clarification_history |
| 6. 召回库写入 | `_update_session_recall_memory` | 成功查询（无错误/拒答/验证通过） | SessionMemory v2（Chroma + JSON） |

### 4.2 会话历史写入

**文件**：`src/api/query.py` 的 `event_stream()` 函数
**触发条件**（`_should_write_session_turn()`）：

- 非反问挂起状态
- 有 `final_sql`
- 非 `fix_failed` 状态

满足条件时调用 `session.add_turn(turn_data)` 写入会话轮次。

---

## 5. Memory 使用机制（加载到上下文）

### 5.1 加载到 Graph State

在 `query.py` 的 `query_endpoint` 中，每次请求时：

```python
# 1. 获取/创建会话
session = session_manager.get_or_create_session(body.session_id, body.user_id)

# 2. 获取用户记忆
user_memory = get_user_memory(body.user_id)

# 3. 构建初始 state
initial_state = create_initial_state(...)
recent_turns = session.get_recent_turns(n=5)  # 最近 5 轮会话历史
initial_state["conversation_history"] = [t for t in recent_turns]
initial_state["metric_definitions"] = user_memory.get_metric_definitions(min_confidence=0.7)
```

### 5.2 ContextVar 跨节点传递

`UserMemory` 和 `SessionMemory` 实例是 Python 对象，不能放进 LangGraph state（checkpointer 序列化会报错）。采用 **ContextVar** 机制：

```python
# src/api/streaming.py
current_user_memory: ContextVar = ContextVar("current_user_memory", default=None)
current_session_memory: ContextVar = ContextVar("current_session_memory", default=None)

# run_graph 中设置
um_token = current_user_memory.set(user_memory)
sm_token = current_session_memory.set(session)
```

节点通过 `get_user_memory_ctx()` / `get_session_memory_ctx()` 获取：

```python
# main_graph.py
session_memory = get_session_memory_ctx()
user_memory = get_user_memory_ctx()
```

### 5.3 各节点如何使用记忆

| 节点 | 使用的记忆 | 来源 |
|------|-----------|------|
| `history_cache` | `conversation_history` + `metric_definitions` + session 召回 | state + ContextVar |
| `cg`（SQL 生成） | `query_preferences` + `metric_definitions` + `historical_sql_refs` | ContextVar + state |
| `memory_update` | UserMemory + SessionMemory 实例 | ContextVar |

---

## 6. HistoryCache 缓存命中检测

**文件**：`src/memory/history_cache.py`
**触发时机**：主图第一个节点，IR 之前
**核心逻辑**：LLM 判断当前查询是否与历史等价

### 6.1 输入输出

**输入**：
- `user_query` —— 当前查询
- `session_history` —— 优先用 `HybridSessionRetriever.retrieve()` 的召回结果，为空时 fallback 到 `conversation_history`
- `metric_definitions` —— 用户长期记忆中的指标定义（跨会话）

**输出**（`CacheResult`）：

```python
CacheResult(
    hit=True / False,
    cached_sql="复用的 SQL",
    source="session_history" | "metric_definition",
    confidence=0.0~1.0,
    historical_query="命中的历史 query",
    matched_metric_name="命中的指标名",
)
```

### 6.2 安全边界

- `confidence < 0.8` 时降级为不命中
- `cached_sql` 为空时降级为不命中

### 6.3 命中后链路

```
history_cache（命中）
  → value_rewrite（值参数改写）
    → cache_confirm（interrupt 反问用户确认）
      → run_single_query → execution（用 cached_sql 直接执行，跳过 IR/SS/CG）
        → memory_update
```

### 6.4 用户否定时

`cache_hit=False` + `cached_sql=None`，回退到完整 IR/SS/CG 链路，但 `historical_sql_refs` 保留作为 few-shot 候选。

---

## 7. 前端 Memory 展示

### 7.1 UserMemoryView 组件

**文件**：`frontend/src/components/UserMemoryView/index.tsx`

- 监听 `userId` 变化，并行调用 `getUserMemory` + `getUserMetrics`
- 分 6 个区块展示 UserMemory 的六维结构
- 用 AntD Table 展示指标定义列表（含名称/描述/SQL 模式/来源/置信度）
- 处理加载态（Spin）、错误态（Alert）、空态（Empty）

### 7.2 状态管理

**文件**：`frontend/src/store/useChatStore.ts`

- 持有 `userMemory: UserMemoryResponse | null` 和 `userMetrics: MetricDefinitionResponse | null`
- 提供 `setUserMemory` / `setUserMetrics` 写入方法

### 7.3 API 接口

**文件**：`frontend/src/api/rest.ts`

- `getUserMemory(userId)` —— `GET /api/v1/users/{id}/memory`
- `getUserMetrics(userId)` —— `GET /api/v1/users/{id}/metrics`

### 7.4 类型定义

**文件**：`frontend/src/api/types.ts`

- `UserMemoryResponse` 接口：镜像后端 Pydantic schema
- `MetricDefinition` 接口：指标定义
- `CacheCheckEvent` SSE 事件类型（含 `hit`, `source`, `confidence`, `cached_sql` 等）

---

## 8. 关键配置项

| 配置项 | 位置 | 默认值 | 说明 |
|--------|------|--------|------|
| `MEMORY_DIR` | `.env` | `./memory` | 记忆存储根路径 |
| `SESSION_RECALL_DENSE_TOP_K` | `deps.py` | 10 | Dense 召回 top_k |
| `SESSION_RECALL_BM25_TOP_K` | `deps.py` | 10 | BM25 召回 top_k |
| `SESSION_RECALL_RRF_K` | `deps.py` | 60 | RRF 融合参数 k |
| `SESSION_RECALL_RRF_THRESHOLD` | `deps.py` | 0.015 | RRF 分数阈值 |
| `SESSION_RECALL_REQUIRE_MULTI` | `deps.py` | False | 是否要求双通道命中 |
| `HistoryCache.min_confidence` | `deps.py` | 0.8 | 缓存命中置信度阈值 |
| `UserMemory.get_metric_definitions.min_confidence` | `query.py` | 0.7 | 指标定义读取阈值 |

---

## 9. 安全机制

### 9.1 字段过滤

| 机制 | 说明 |
|------|------|
| `UserMemory._BLOCKED_MEMORY_KEYS` | 禁止 `final_result`, `result`, `llm_thinking`, `graph_state` 等大字段进入长期记忆 |
| `UserMemory._sanitize_mapping()` | 写入前过滤非法字段 |
| `UserMemory._normalize_memory()` | 加载时规范化，丢弃未知顶层 key |
| `SessionMemory._ALLOWED_TURN_FIELDS` | 白名单过滤，仅允许写入必要字段，`final_result` 不存储 |

### 9.2 双轨制防覆盖

`user_taught` 来源的指标定义不会被 `auto_learned` 覆盖：

```python
if key in existing and existing[key].get("source") == "user_taught" and source == "auto_learned":
    return  # 用户教导的条目不会被自动学习覆盖
```

### 9.3 文件锁与原子写入

- 跨平台文件锁防止并发写入冲突
- 先写 `.tmp` 再 `os.replace()` 原子替换，确保写入不损坏文件
- 读取失败时尝试读取 `.tmp` 备份

---

## 10. 测试覆盖

| 测试文件 | 路径 | 覆盖内容 |
|---------|------|---------|
| `test_user_memory.py` | `tests/memory/test_user_memory.py` | UserMemory 创建/加载/六维操作/安全过滤 |
| `test_session_memory.py` | `tests/memory/test_session_memory.py` | SessionMemory 轮次管理/白名单过滤 |
| `test_session_manager.py` | `tests/memory/test_session_manager.py` | SessionManager CRUD/LRU 缓存 |
| `test_memory_updater.py` | `tests/memory/test_memory_updater.py` | 自动学习各子步骤 |
| `test_history_cache.py` | `tests/memory/test_history_cache.py` | 命中/未命中/置信度过滤 |
| `test_session_recall.py` | `tests/memory/test_session_recall.py` | Dense/BM25/RRF 混合召回 |
| `e2e_live_with_memory.py` | `tests/e2e_live_with_memory.py` | 端到端记忆测试 |

---

## 11. 架构总图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    Claude Code 项目记忆（框架层）                            │
│  C:\Users\WangHongZe\.claude\projects\...\memory\                          │
│    MEMORY.md（索引） + *.md（具体记忆内容）                                  │
│  管理：Claude Code 自动读写                                                 │
│  用途：跨会话保留项目上下文（前端架构/bug 记录/用户偏好）                     │
│  生成时机：用户主动要求 / 对话中自主判断需要持久化的信息                     │
│  使用机制：会话启动时 MEMORY.md 全文注入 context                             │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                        NL2SQL 应用记忆系统（业务层）                         │
│  D:\CodeProjects\PycharmProjects\NL2SQL\src\memory\                        │
│  存储：./memory/（由 MEMORY_DIR 配置，.gitignore 排除）                      │
│                                                                             │
│  ┌──────────────────┐    ┌─────────────────────┐    ┌──────────────────┐   │
│  │   UserMemory     │    │   SessionMemory     │    │ SessionMemory v2 │   │
│  │   （长期记忆）   │    │   （会话记忆）      │    │   （混合召回）   │   │
│  ├──────────────────┤    ├─────────────────────┤    ├──────────────────┤   │
│  │ 6 维结构化数据  │    │ 对话轮次列表        │    │ Chroma 向量索引  │   │
│  │ 跨会话持久化     │    │ 上下文摘要          │    │ BM25 本地检索    │   │
│  │ 双轨制指标定义   │    │ 白名单字段过滤      │    │ RRF 融合排序     │   │
│  │ 安全过滤黑名单   │    │ LRU 缓存（200 个）  │    │ session 隔离过滤 │   │
│  │ 文件锁+原子写入  │    │ 文件锁+原子写入     │    │               │   │
│  └────────┬─────────┘    └──────────┬──────────┘    └────────┬─────────┘   │
│           │                        │                         │             │
│           └────────────────────────┼─────────────────────────┘             │
│                                    │                                       │
│                          ┌─────────▼──────────┐                            │
│                          │   MemoryUpdater     │                            │
│                          │   （自动学习）      │                            │
│                          │   6 个子步骤        │                            │
│                          │   主图末尾触发      │                            │
│                          └─────────┬──────────┘                            │
│                                    │                                       │
│                          ┌─────────▼──────────┐                            │
│                          │   HistoryCache      │                            │
│                          │   （历史命中检测）  │                            │
│                          │   主图首个节点      │                            │
│                          │   2 种命中来源      │                            │
│                          │   3 层安全边界      │                            │
│                          └─────────────────────┘                            │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  ContextVar 机制                                                      │  │
│  │  current_user_memory / current_session_memory                         │  │
│  │  用途：跨 LangGraph 节点传递 Python 对象（避免 checkpointer 序列化问题）│  │
│  └──────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘

                         写入时机：每次 graph 执行完成后 memory_update 节点
                         读取时机：每次请求 query_endpoint 时注入 state + ContextVar
```
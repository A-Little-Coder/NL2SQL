<!-- last-updated: 2026-06-13 -->

# NL2SQL — 自然语言转 SQL 智能 Agent

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![LangGraph](https://img.shields.io/badge/built%20with-LangGraph-6C5CE7.svg)](https://langchain-ai.github.io/langgraph/)
[![LangSmith](https://img.shields.io/badge/monitoring-LangSmith-FF6B6B.svg)](https://smith.langchain.com/)

**NL2SQL** 是一个基于 LangGraph 编排的多 Agent 协作系统，能够将用户的中文自然语言问题自动转换为 SQL 查询语句，执行并返回结果。系统采用流水线（pipeline）架构，依次经过意图解析、Schema 召回、Schema 选择、SQL 生成、执行和自洽性决策等多个阶段，每个阶段由一个独立的子图 Agent 负责。

> **核心能力：**
> - 多数据库支持（SQLite / MySQL），按 `db_id` 隔离
> - 中文自然语言 → SQL 的端到端转换
> - SSE 流式响应（实时推送每个节点的执行进度）
> - 智能修复循环（SQL 执行失败时自动诊断并重试）
> - 自洽性决策（多候选 SQL 评分 + 并列重评）
> - 会话历史与用户记忆持久化
> - 可回答性检查（在 Schema 就绪后判断问题是否可回答）
> - LangSmith 全链路追踪 + LangGraph Studio 热重载调试

---

## 目录

- [快速开始](#快速开始)
- [架构总览](#架构总览)
- [模块参考](#模块参考)
- [API 参考](#api-参考)
- [LangSmith 监控](#langsmith-监控)
- [LangGraph Studio 调试](#langgraph-studio-调试)
- [测试](#测试)
- [配置参考](#配置参考)

---

## 快速开始

### 环境准备

- Python 3.10 或更高版本（推荐 3.10）
- 推荐使用 conda 管理虚拟环境

### 克隆项目

```bash
git clone <repo-url>
cd NL2SQL
```

### 安装依赖

```bash
conda create -n NL2SQL python=3.10 -y
conda activate NL2SQL
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 配置环境变量

复制 `.env.example` 为 `.env` 并填入密钥：

| 环境变量 | 是否必填 | 说明 |
|----------|----------|------|
| `QWEN_API_KEY` | ✅ | 通义千问 API 密钥（DashScope） |
| `BGE_M3_MODEL_PATH` | ✅ | BGE-M3 本地路径或 HuggingFace 模型名 |
| `LANGCHAIN_API_KEY` | ❌ | LangSmith API 密钥（无则不开启 trace） |
| `TAVILY_API_KEY` | ❌ | Tavily 搜索 API（Clarification Agent 用） |

### 构建索引

每个数据库在接入前需要构建 LSH 索引和向量索引：

```bash
python src/preprocessing/build_lsh_index.py --db_id <database_id>
python src/preprocessing/build_schema_index.py --db_id <database_id>
```

### 启动服务

```bash
python run_api.py
```

服务默认在 `http://localhost:8000` 启动，Swagger 文档位于 `http://localhost:8000/docs`。

### 启动前端（问数界面）

前台工程位于 `frontend/`（React + Vite + TypeScript + Ant Design），消费后端 REST + SSE 接口，把 Agent 推理流实时渲染成"玻璃箱数据分析师"体验（三栏可拖拽布局 + SSE 时间轴 + 反问 resume + IR/SS 透明展示）。

```bash
cd frontend
npm install --registry https://registry.npmmirror.com   # 首次需安装依赖
npm run dev
```

前端 dev server 默认在 `http://localhost:5173`（如被占用自动顺延到 5174/5175），Vite 自动 proxy `/api/v1` -> `http://localhost:8000`，无需处理跨域。浏览器访问 `http://localhost:5173` 即可。

### 一体化启动顺序

1. 先启动后端：`python run_api.py`（等"Application startup complete"）
2. 再启动前端：`cd frontend && npm run dev`
3. 浏览器打开前端地址，顶部确认 DB 已选中（如 `california_schools`），底部输入问题即可

> 前端首次访问冷库时，后端懒加载约 5-10 秒，界面会提示"首次加载该数据库"。

---

## 架构总览

### 系统框图

```
用户查询 ──→ [API Gateway]
                │
                ▼
         ┌──────────────┐
         │  HistoryCache │─── 命中则直接返回缓存结果
         └──────┬───────┘
                │ (cache miss)
                ▼
         ┌──────────────┐      ┌──────────────────┐
         │   IR 子图     │─────▶│  Keyword 提取     │
         │ (Information  │      │  Schema 向量召回  │
         │  Retrieval)   │      │  LSH 精确召回     │
         └──────┬───────┘      └──────────────────┘
                │ (召回结果)
                ▼
         ┌──────────────┐
         │ Clarification │─── 需澄清则对话交互，否则 pass-through
         └──────┬───────┘
                │
                ▼
         ┌──────────────┐
         │   SS 子图     │─────▶  LLM 选择相关表/列
         │ (Schema       │
         │  Selection)   │
         └──────┬───────┘
                │
                ▼
         ┌─────────────────┐
         │ Answerability    │─── 不可回答 → 拒答（含原因）
         │ Check            │
         └──────┬──────────┘
                │ (可回答)
                ▼
         ┌──────────────┐
         │   CG 子图     │─────▶  生成 N 个候选 SQL
         │ (SQL          │       (N=3，由 LLM 生成)
         │  Generation)  │
         └──────┬───────┘
                │
                ▼
         ┌──────────────┐
         │ Execution     │─────▶  执行候选 SQL
         │ (Executor)    │       失败 → SmartFix 重试
         └──────┬───────┘
                │
                ▼
         ┌──────────────┐
         │ Decision      │─────▶  R1 评分 → R2 并列重评
         │ (Self-        │       最终决策 → Result Verify
         │  Consistency) │       结果不可信 → 拒答
         └──────┬───────┘
                │
                ▼
         ┌──────────────┐
         │ Memory Update │─────▶  更新会话历史 + 用户记忆
         └──────┬───────┘
                │
                ▼
            [SSE Response]  ──→  返回 SQL + 结果到客户端
```

### 子图拓扑细节

| 子图 | 入口模块 | 核心节点 |
|------|----------|----------|
| **IR** | `src/retrieval/ir_graph.py` | 关键词提取 → Embedding 召回 → LSH 召回 → 混合排序 |
| **SS** | `src/schema_selection/ss_graph.py` | LLM 选择 → 结果校验 |
| **CG** | `src/sql_generation/cg_graph.py` | 多候选生成 → 格式校验 |
| **Execution** | `src/execution/execution_graph.py` | SQL 执行 → SmartFix 循环（最多 3 轮） |
| **Decision** | `src/decision/decision_graph.py` | 数据评分(R1) → SQL 评分(R2) → 最终决策 → 结果验证 |

---

## 模块参考

| 包路径 | 职责 |
|--------|------|
| `src/api/` | FastAPI 服务层：路由、SSE 流式响应、DB 连接池、依赖注入 |
| `src/graph/` | LangGraph 主图编排：State 定义、节点包装、流水线构建 |
| `src/retrieval/` | 信息召回子图：关键词提取、向量召回、LSH 精准召回、混合排序 |
| `src/clarification/` | 对话澄清 Agent：有歧义时主动追问（Phase 2 功能，当前为 pass-through） |
| `src/schema_selection/` | Schema 选择子图：基于召回结果用 LLM 选取相关表和列 |
| `src/verification/` | 可回答性检查 + 结果验证 |
| `src/sql_generation/` | SQL 生成子图：生成 N 个候选 SQL + 基础校验 |
| `src/execution/` | SQL 执行子图：多引擎执行 + SmartFix 自动修复 |
| `src/decision/` | 自洽性决策子图：多候选评分、并列重评、最终决策、结果验证 |
| `src/memory/` | 持久化存储：会话管理、用户记忆、SessionMemory v2 混合召回、历史缓存、Memory Updater |
| `src/preprocessing/` | 离线预处理：数据库连接、Schema 向量化、LSH 索引构建、增量更新 |
| `utils/` | 共享工具：LLM 客户端封装（Qwen / OpenAI 协议兼容） |


## 记忆与历史召回

系统包含两类记忆：

- **SessionMemory**：会话级短期记忆。成功查数的历史 query 会写入 SessionMemory v2 召回库；召回时先按当前 `user_id`、`session_id`、`db_id`、`success=true` 过滤，只在本 session 内执行 BGE-M3 向量召回和本地 BM25 召回，再通过 RRF 融合排序。只有 `rrf_score >= SESSION_RECALL_RRF_THRESHOLD` 的记忆会进入 HistoryCache 判断。
- **UserMemory**：用户长期偏好记忆，固定 JSON topics：`term_preferences`、`frequently_used_tables`、`metric_definitions`、`query_preferences`、`domain_context`、`clarification_history`。系统会过滤 few-shot 示例、结果数据和中间 graph state，避免污染长期记忆。

SessionMemory v2 的 demo 存储实现为：

| 层级 | demo 实现 | 说明 |
|------|-----------|------|
| Query Recall Index | Chroma collection `nl2sql_session_queries` | 存 query embedding 和过滤 metadata |
| Conversation Store | `data/session_memory_v2/` JSON 文件 | 存 query/final_sql 等无结果历史对话 |
| 融合排序 | 本地 RRF | 允许单路召回，最终按 RRF 阈值过滤 |

相关配置：

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `SESSION_RECALL_DENSE_TOP_K` | `10` | 本 session 向量召回数量 |
| `SESSION_RECALL_BM25_TOP_K` | `10` | 本 session BM25 召回数量 |
| `SESSION_RECALL_RRF_K` | `60` | RRF 排序参数 |
| `SESSION_RECALL_RRF_THRESHOLD` | `0.015` | RRF 召回阈值 |
| `SESSION_RECALL_REQUIRE_MULTI` | `false` | 是否要求 dense 与 BM25 双路同时命中 |

---


### 启动服务

```bash
# 基础启动（所有 db 按需懒加载）
python run_api.py

# 指定端口
python run_api.py --port 8080

# 启动并预加载指定数据库
python run_api.py --db_id california_schools
```

### 查询接口

```
POST /api/v1/query
```

请求体（JSON）：

```json
{
  "query": "查询加州各个学校的平均成绩",
  "session_id": "user-session-001",
  "user_id": "alice",
  "db_id": "california_schools"
}
```

响应为 **SSE（Server-Sent Events）** 流式格式，每个事件一行：

```
data: {"type": "stage", "data": {"node": "ir", "status": "started", "query_id": "abc123..."}}

data: {"type": "stage", "data": {"node": "ir", "status": "done", "query_id": "abc123..."}}

data: {"type": "keywords", "data": {"groups": [...], "query_id": "abc123..."}}

data: {"type": "stage", "data": {"node": "ss", "status": "started", "query_id": "abc123..."}}
...
data: {"type": "result", "data": {"sql": "SELECT ...", "result": [...], "query_id": "abc123..."}}

data: {"type": "done", "data": {"has_result": true, "query_id": "abc123..."}}
```

**事件类型：**

| 事件类型 | 含义 |
|----------|------|
| `stage` | 节点开始/结束（node + status） |
| `cache_check` | 历史缓存命中检测 |
| `keywords` | IR 关键词提取结果 |
| `schema_recall` | Schema 召回结果 |
| `llm_thinking` | Qwen 思考链片段 |
| `answerability` | 可回答性判定 |
| `sql_candidates` | CG 候选 SQL |
| `execution` | SQL 执行结果 |
| `final_decision` | 最终决策 |
| `result` | 最终 SQL + 数据结果 |
| `error` | 错误信息 |
| `done` | 整条查询完成 |

> **注意：** SSE 流式响应要求客户端不要关闭读取超时。推荐使用 `httpx.Timeout(connect=10, read=None)` 或依赖每 15 秒的心跳保活。

### 健康检查

```bash
GET /api/v1/health
```

### 其他接口

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/health` | GET | 健康检查 |
| `/api/v1/query` | POST | 核心查询（SSE 流式） |
| `/api/v1/databases` | GET | 获取已接入的数据库列表 |
| `/api/v1/databases/{db_id}/tables` | GET | 获取指定数据库的表清单 |
| `/api/v1/session/{session_id}` | GET | 获取会话历史 |
| `/api/v1/user/{user_id}/memory` | GET | 获取用户记忆 |
| `/api/v1/user/{user_id}/metrics` | GET | 获取用户指标定义 |

### curl 示例

```bash
# 查询
curl -N -X POST http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -d '{"query": "查询各校平均成绩", "session_id": "demo", "user_id": "alice", "db_id": "california_schools"}'

# 健康检查
curl http://localhost:8000/api/v1/health
```

---

## LangSmith 监控

项目已接入 **LangSmith Path A** 自动链路追踪。所有 LangGraph 图执行过程会自动上报到 LangSmith。

### 配置方式

在 `.env` 中配置：

```env
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=lsv2_pt_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
LANGCHAIN_PROJECT=NL2SQL
```

### 链路内容

每次查询在 LangSmith 中会记录：

- **Run name**：`query-{query_id}`（如 `query-abc123def456`）
- **Tags**：`{db_id}`、`api`、`user:{user_id}` 等
- **Metadata**：query_id、user_id、session_id、db_id、user_query
- **Thread ID**：`session_id`（按会话聚合）
- **每个节点的输入/输出**：IR 的关键词、SS 选择的 schema、CG 候选 SQL、Decision 评分等

### 查看方式

1. 前往 [smith.langchain.com](https://smith.langchain.com)
2. 选择项目 `NL2SQL`
3. 按 `run_name` 搜索特定查询（如 `query-abc123`）
4. 或按 `tag` 过滤（如 tag=california_schools）

---

## LangGraph Studio 调试

LangGraph Studio 提供图形化界面，可在本地查看图拓扑、节点 I/O、fork 重跑，并支持代码热重载。

### 启动

```bash
# 确保已安装 langgraph-cli[inmem]
pip install "langgraph-cli[inmem]" -i https://pypi.tuna.tsinghua.edu.cn/simple

# 启动 Studio 开发服务器
langgraph dev
```

启动后终端会显示两个地址：
- **API**：`http://127.0.0.1:2024`
- **Studio UI**：`https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024`

### Studio 功能

| 功能 | 说明 |
|------|------|
| **拓扑可视化** | 自动渲染 LangGraph 主图 + 子图的结构、节点依赖、条件边 |
| **节点 I/O 查看** | 点击任意节点查看执行时的 Input State 和 Output |
| **Fork 重跑** | 从任意中间节点 fork，修改 state 后重新执行下游 |
| **热重载** | 修改代码后自动重新编译，无需手动重启 |
| **多 DB 测试** | 在 Configuration 面板中选择 `db_id` 切换目标数据库 |

### 调试入口

Studio 的入口文件为 `src/graph/studio_entry.py`，每次启动会通过 `DbContextPool` 按需加载指定数据库的图实例。支持在 Configuration 面板中选择 `db_id`。

### 注意事项

- Studio 是**本地开发调试工具**，不替代生产 `run_api.py` 服务
- 不会向 Studio 桥接 `query_id`、用户记忆、会话历史等请求级注入
- 如果看到拓扑中缺少某些节点，请检查 `langgraph.json` 中的图入口名是否与 `pyproject.toml` 中注册的 graph 名称一致

---

## 测试

### 运行测试

```bash
# 运行全量测试
python -m pytest tests/ --tb=short -q

# 仅运行 API 测试
python -m pytest tests/api/ --tb=short -q

# 仅运行特定模块测试
python -m pytest tests/decision/ --tb=short -q
python -m pytest tests/retrieval/ --tb=short -q

# 运行带心跳的长测试（默认跳过）
SKIP_HEARTBEAT_TEST=0 python -m pytest tests/api/test_query_stream.py -k heartbeat --tb=short -q
```

### 测试目录结构

```
tests/
├── api/                  # API 集成测试（SSE 流式、query_id、多 DB 等）
├── clarification/        # 澄清 Agent 测试
├── decision/             # 自洽性决策子图测试
├── execution/            # 执行器 + SmartFix 测试
├── graph/                # 主图编排测试
├── memory/               # 会话、用户记忆、缓存测试
├── preprocessing/        # 离线预处理测试
├── retrieval/            # 信息召回子图测试
├── schema_selection/     # Schema 选择子图测试
├── sql_generation/       # SQL 生成子图测试
├── utils/                # LLM 客户端工具测试
├── verification/         # 可回答性 + 结果验证测试
├── manual/               # 人工手动测试脚本
├── e2e_live.py           # 端到端线上测试（需数据库环境）
├── test_e2e_mock.py      # 端到端 Mock 测试
└── test_langsmith_integration.py  # LangSmith 集成测试
```

### 添加新测试

- 每个模块的测试文件放在 `tests/<module>/` 目录下
- 测试文件以 `test_` 开头
- 推荐使用 `pytest` fixture 管理依赖注入
- API 测试使用 `fastapi.testclient.TestClient` + `monkeypatch` 模拟依赖

---

## 配置参考

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| **LLM 配置** | | |
| `QWEN_API_KEY` | — | 通义千问 API 密钥 |
| `QWEN_BASE_URL` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | API 端点 |
| `QWEN_MODEL` | `qwen3.6-plus` | 模型名称 |
| `LLM_ENABLE_THINKING` | `true` | 是否启用思考链推送 |
| **Embedding 模型** | | |
| `BGE_M3_MODEL_PATH` | `BAAI/bge-m3` | BGE-M3 模型路径（本地或 HuggingFace） |
| **LangSmith 监控** | | |
| `LANGCHAIN_TRACING_V2` | `true` | 启用 LangSmith trace |
| `LANGCHAIN_API_KEY` | — | LangSmith API 密钥 |
| `LANGCHAIN_PROJECT` | `NL2SQL` | LangSmith 项目名称 |
| **联网搜索** | | |
| `TAVILY_API_KEY` | — | Tavily 搜索 API 密钥 |
| **服务配置** | | |
| `DB_POOL_MAX_SIZE` | `2` | DbContextPool 最大容量 |
| `SSE_HEARTBEAT_INTERVAL` | `15` | SSE 心跳间隔（秒） |

---

## 技术栈

- **语言框架：** Python 3.10, LangGraph 1.x, LangChain Core 1.x
- **API 服务：** FastAPI, Uvicorn, SSE (Server-Sent Events)
- **向量存储：** ChromaDB, BGE-M3 (FlagEmbedding)
- **精确索引：** LSH (datasketch MinHash), SQLite FTS5
- **LLM：** Qwen (DashScope API), OpenAI 协议兼容
- **SQL 处理：** SQLGlot (解析/校验), SQLite / MySQL
- **监控：** LangSmith
- **调试：** LangGraph Studio (langgraph dev)
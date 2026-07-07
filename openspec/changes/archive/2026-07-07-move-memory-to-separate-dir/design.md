## Context

当前记忆存储全部位于 `data/` 目录下，与预处理数据（schema 向量索引等）混在一起。记忆数据包括用户长期记忆、会话历史、向量化的历史 query，具有运行时动态写入、可恢复、可清理的特性。预处理数据则是静态的、构建一次后只读的。

`data/` 当前结构：
```
data/
├── sessions/{user_id}/{session_id}.json        ← 会话记忆 v1
├── user_memory/{user_id}.json                   ← 用户长期记忆
├── session_memory_v2/{user_id}/{session_id}.json ← 会话记忆 v2 JSON
└── preprocessed/
    └── chroma/                                  ← Chroma 向量索引
        ├── nl2sql_columns (collection)          ← schema 列向量（静态）
        └── nl2sql_session_queries (collection)  ← 历史 query 向量（动态）
```

## Goals / Non-Goals

**Goals:**
- 新建 `memory/` 目录，将所有运行时记忆数据从 `data/` 迁出
- Chroma 中 `nl2sql_session_queries` collection 单独建库到 `memory/chroma/`
- 通过 `.env` 配置记忆目录路径（`MEMORY_DIR`），代码不再硬编码
- 编写一次性迁移脚本搬移已有数据，运行后自毁
- 修改所有代码中涉及的记忆路径

**Non-Goals:**
- 不改变记忆的数据格式或存储方式（JSON、Chroma 不变）
- 不改变 `data/preprocessed/` 中的其他数据
- 不涉及 schema 变更或 API 接口变更
- 不涉及记忆功能的逻辑行为变更

## Decisions

### 决策 1：Chroma 拆分方式

**方案**：使用两个独立的 Chroma 持久目录。

当前 Chroma 一个目录下放了两个 collection，直接按 collection 删除和重建为新目录。迁移脚本通过 Chroma Python API 读取旧 collection 的全部数据（含 embeddings），写入新库，然后删除旧 collection。

```python
# Chroma 迁移逻辑
old_client = chromadb.PersistentClient(path="data/preprocessed/chroma")
old_col = old_client.get_collection("nl2sql_session_queries")
data = old_col.get(include=["embeddings", "documents", "metadatas"])

new_client = chromadb.PersistentClient(path="memory/chroma")
new_col = new_client.create_collection("nl2sql_session_queries")
new_col.add(ids=data["ids"], embeddings=data["embeddings"],
            documents=data["documents"], metadatas=data["metadatas"])

old_client.delete_collection("nl2sql_session_queries")
```

**替代方案**：用一个共享 Chroma 目录。否决原因——失去了分离的语义，且 data 目录清理会连带清掉记忆向量。

### 决策 2：路径配置方式

**方案**：`.env` 文件 + `os.getenv`。在 `deps.py` 中加载：

```python
MEMORY_DIR = os.getenv("MEMORY_DIR", "memory")
memory_dir = project_root / MEMORY_DIR
```

`data_dir` 保持默认不变（`project_root / "data"`），用于预处理数据。

使用 `python-dotenv` 的 `load_dotenv()` 加载 `.env`（如果项目尚未使用，则引入该最小依赖）。

**替代方案**：YAML 配置文件。否决原因——对于单一路径配置来说过于复杂。环境变量已足够。

### 决策 3：迁移脚本自毁方式

**方案**：迁移脚本运行后，用户删除它（由任务显式要求用户操作）。脚本不自动删除自身（平台兼容性问题）。

## Risks / Trade-offs

| 风险 | 缓解措施 |
|------|---------|
| 迁移脚本运行时当前查询正在进行 | 迁移脚本要求停止 API 服务后再运行 |
| Chroma embedding 数据过大导致迁移慢 | 使用 `include=["embeddings"]` 批量读取，一次性写入 |
| 旧 Chroma collection 删除失败 | 脚本提示用户手动删除，不自动阻塞流程 |
| VectorStoreManager 并发 Chroma 初始化 | 迁移脚本单独创建 Chroma client，不经过 VectorStoreManager |

## Migration Plan

1. 修改代码中的路径配置 → 代码部署
2. 运行迁移脚本 `python scripts/migrate_memory.py`
3. 重启 API 服务
4. 验证记忆读写正常
5. 脚本运行提示用户删除 `data/sessions/`、`data/user_memory/`、`data/session_memory_v2/` 旧目录
6. 删除迁移脚本自身

## Open Questions

- 是否需要为 `python-dotenv` 添加 pip 依赖？如项目已启动时自动加载 `.env` 则无需
- MemoryUpdater 中 `_update_session_recall_memory` 写入 Chroma 时是否要保证 memory/chroma/ 目录已存在？需要代码中隐式创建路径
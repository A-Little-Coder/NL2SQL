## Why

当前项目将所有数据（预处理 schema 静态数据 + 运行时记忆）都存放在 `data/` 目录下，职责不清。运行时记忆（会话历史、用户偏好、向量化的历史 query）与预处理数据（schema 列向量索引）混在一起，不利于运维、备份和清理。需要将记忆数据独立到 `memory/` 目录，与预处理数据职责分离。

## What Changes

- 在项目根目录新建 `memory/` 目录，存放所有运行时记忆数据
- 将 `data/sessions/`（会话历史 v1）迁移到 `memory/sessions/`
- 将 `data/user_memory/`（用户长期记忆）迁移到 `memory/user_memory/`
- 将 `data/session_memory_v2/`（会话历史 v2 JSON）迁移到 `memory/session_memory_v2/`
- 将 Chroma 中 `nl2sql_session_queries` collection 从 `data/preprocessed/chroma/` 迁出到 `memory/chroma/`
- 新增 `.env` 配置文件，通过 `MEMORY_DIR` 环境变量配置记忆目录路径
- 修改代码中所有硬编码的记忆存储路径，改为从 `.env` 读取
- 编写一次性迁移脚本 `scripts/migrate_memory.py`，运行后自毁
- 不动的部分：`data/preprocessed/chroma/` 中的 `nl2sql_columns` collection 及其它预处理数据

## Capabilities

### New Capabilities

- `memory-storage`: 运行时记忆数据的独立存储策略，包括目录结构、路径配置、迁移机制

### Modified Capabilities

- （无，本次不改变 spec 级行为，只改变存储实现）

## Impact

- **代码**: `src/api/deps.py` 中记忆路径从 `data_dir` 派生改为从 `MEMORY_DIR` 派生；`src/memory/user_memory.py` 默认 `base_dir` 变更；`src/memory/session_recall.py` 中 Chroma 持久目录变更；`src/preprocessing/vector_store.py` 新增 `delete_collection` 接口
- **测试**: 测试文件中的硬编码路径需同步更新
- **运维**: 运行迁移脚本后建议删除 `data/sessions/`、`data/user_memory/`、`data/session_memory_v2/` 旧目录
- **配置**: 新增 `.env` 文件；`MEMORY_DIR` 的默认值保持向后兼容（`./memory`）
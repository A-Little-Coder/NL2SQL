## 1. 路径配置与代码修改

- [x] 1.1 在 `src/api/deps.py` 中读取 `MEMORY_DIR` 环境变量，推导出 `memory_dir` 路径
- [x] 1.2 将 `SessionManager` 的 `base_dir` 从 `data_dir / "sessions"` 改为 `memory_dir / "sessions"`
- [x] 1.3 将 `get_user_memory` 中 `UserMemory` 的 `base_dir` 从 `data_dir / "user_memory"` 改为 `memory_dir / "user_memory"`
- [x] 1.4 将 `JsonConversationStore` 的 `base_dir` 从 `data_dir / "session_memory_v2"` 改为 `memory_dir / "session_memory_v2"`
- [x] 1.5 将 `ChromaSessionQueryIndex` 的 `persist_directory` 从 `data_dir / "preprocessed" / "chroma"` 改为 `memory_dir / "chroma"`

## 2. VectorStoreManager 新增接口

- [x] 2.1 在 `src/preprocessing/vector_store.py` 中 `VectorStoreManager` 新增 `delete_collection(collection_name: str)` 方法

## 3. 迁移脚本

- [x] 3.1 创建 `scripts/migrate_memory.py`，实现以下逻辑：
  - 搬运 `data/sessions/` → `memory/sessions/`
  - 搬运 `data/user_memory/` → `memory/user_memory/`
  - 搬运 `data/session_memory_v2/` → `memory/session_memory_v2/`
  - 从旧 Chroma 读取 `nl2sql_session_queries` collection 写入新 Chroma
  - 删除旧 Chroma 中的 `nl2sql_session_queries` collection
  - 提示用户手动清理旧目录
  - 源目录不存在时跳过并记录警告

## 4. 环境配置

- [x] 4.1 创建 `.env` 文件，写入 `MEMORY_DIR=./memory`
- [x] 4.2 如项目未使用 `python-dotenv`，在 `src/api/deps.py` 入口处添加 `load_dotenv()`

## 5. 测试文件路径修正

- [x] 5.1 检查并修正 `tests/memory/test_session_recall.py` 中的硬编码路径（使用 tmp_path，无需修改）
- [x] 5.2 检查并修正 `tests/memory/test_memory_updater.py` 中的硬编码路径（`data/test_session_recall_tmp` → `tmp_path`）
- [x] 5.3 检查并修正其他测试文件中与记忆路径相关的硬编码（`e2e_live_with_memory.py` 中 sessions 和 user_memory 路径）

## 6. 迁移执行与验证

- [x] 6.1 运行迁移脚本 `python scripts/migrate_memory.py`
- [x] 6.2 启动 API 服务，验证记忆读写正常（会话历史、用户记忆、历史 query 召回）
- [x] 6.3 确认旧 Chroma 中的 `nl2sql_session_queries` collection 已清理（仅剩 `nl2sql_columns`）
- [x] 6.4 删除 `scripts/migrate_memory.py` 迁移脚本自身
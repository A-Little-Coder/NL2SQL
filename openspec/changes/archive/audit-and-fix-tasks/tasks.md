## 1. 更新主 Change 的 tasks.md（任务状态同步）

- [x] 1.1 将 §2.1（数据库连接管理器）标记为已完成
- [x] 1.2 将 §2.2（LSH 索引生成器）标记为已完成
- [x] 1.3 将 §2.5（schema 列文档生成器）标记为已完成
- [x] 1.4 将 §2.6（离线索引构建脚本）标记为已完成
- [x] 1.5 将 §2.7（列文档生成器测试）标记为已完成
- [x] 1.6 将 §3.7（per-keyword schema 检索）标记为已完成
- [x] 1.7 将 §3.8（LSH + 语义精排值检索）标记为已完成
- [x] 1.8 修正 tasks.md 中 `scripts/` 路径为 `src/preprocessing/`

## 2. 更新主 Change 的 preprocessing spec

- [x] 2.1 更新 `nl2sql-preprocessing.md` spec：collection 命名改为全局 `nl2sql_columns`，持久化目录改为 `data/preprocessed/chroma/`，id 格式改为 `{db_id}.{table_name}.{column_name}`

## 3. 补全基础设施

- [x] 3.1 在 `.gitignore` 中添加 `data/user_memory/*.json` 排除规则
- [x] 3.2 在 `requirements.txt` 中添加 `tavily-python>=0.3.0`
- [x] 3.3 创建 `.env.example` 文件，含 `TAVILY_API_KEY=` 配置项
- [x] 3.4 创建 `data/user_memory/` 目录并添加 `.gitkeep`

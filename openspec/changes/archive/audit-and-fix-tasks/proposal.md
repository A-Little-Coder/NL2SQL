## Why

NL2SQL 项目在开发过程中存在 tasks.md 与实际代码状态不同步的问题：部分已实现功能未标记完成，部分 specs 描述与当前实现存在偏差，部分路径描述不准确。这会影响后续开发的进度追踪和任务规划，需要一次系统性的审计修正。

## What Changes

1. **更新 tasks.md 已完成项**：将 §2 预处理模块中已实现的任务（2.1-2.2, 2.5-2.7）标记为完成
2. **更新 tasks.md 已完成 IR 项**：将 §3 信息检索中已实现的改进（3.7-3.8）标记为完成
3. **修正路径描述**：tasks.md 中 `scripts/` 路径实际为 `src/preprocessing/`
4. **更新 preprocessing spec**：反映当前已实施的全局单 collection 方案
5. **修正 .gitignore**：排除 `data/user_memory/*.json`
6. **更新 requirements.txt**：补充 `tavily-python` 依赖
7. **明确任务状态**：区分"已实现"、"已实现但路径不对"、"未实现"三类

## Capabilities

### New Capabilities
<!-- No new capabilities needed -->

### Modified Capabilities
- `nl2sql-preprocessing`: 更新 spec 以匹配实际实现的全局单 collection 方案（`nl2sql_columns`）

## Impact

- `openspec/changes/nl2sql-agent-system/tasks.md`：多项任务标记状态修正
- `openspec/changes/nl2sql-agent-system/specs/nl2sql-preprocessing.md`：collection 命名、路径、ID 格式更新
- `.gitignore`：新增 `data/user_memory/*.json` 排除规则
- `requirements.txt`：补充 `tavily-python>=0.3.0`

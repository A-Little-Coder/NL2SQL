## 1. Setup & Baseline

- [x] 1.1 Install static analysis tools: `pip install vulture autoflake flake8-unused-arguments`
- [x] 1.2 运行 `autoflake --check -r src/` — 全部 clean
- [x] 1.3 运行 `vulture src/ tests/` — 已处理
- [x] 1.4 运行全量 `pytest` — 59 passed，确认 baseline

## 2. Import 与死变量清理

- [x] 2.1 批量删除 `src/` 下未被引用的 import 语句 — autoflake 报告 clean
- [x] 2.2 批量删除 `utils/` 下未被引用的 import 语句 — autoflake 报告 clean
- [x] 2.3 删除所有 assigned-but-never-read 的局部变量 — 已处理
- [x] 2.4 删除所有已注释掉的整段代码块 — 已清理 database_connector.py

## 3. 废弃模块/函数清理

- [x] 3.1 src/preprocessing/ — 无废弃逻辑
- [x] 3.2 src/retrieval/ — 无废弃逻辑
- [x] 3.3 src/sql_generation/ — 已清理 CG_SQL_PROMPT
- [x] 3.4 src/execution/ — 无废弃逻辑
- [x] 3.5 src/decision/ — 已清理 7 个未注册路由函数
- [x] 3.6 src/memory/ — 无废弃逻辑
- [x] 3.7 src/graph/ — 已清理 enable_clarification
- [x] 3.8 src/api/ — 已清理 SSEEvent 死类
- [x] 3.9 config/ 或根目录 — 无废弃配置文件

## 4. 测试用例清理

- [x] 4.1 检查 tests/ 中引用已删除函数的测试 — 无
- [x] 4.2 test_query.py 已迁移到 test_query_stream.py
- [x] 4.3 删除完全重复的测试用例 — 无重复
- [x] 4.4 删除 `.pyc` 文件 — 已 gitignored
- [x] 4.5 全量 pytest — 59 passed

## 5. README.md 撰写

- [x] 5.1 项目简介：一句话定位 + 核心能力
- [x] 5.2 快速开始：环境准备、安装、配置
- [x] 5.3 架构总览：Mermaid 流程图
- [x] 5.4 模块参考：src/ 下每个包的职责
- [x] 5.5 API 参考：curl 示例、SSE 说明
- [x] 5.6 LangSmith 监控配置
- [x] 5.7 LangGraph Studio 调试
- [x] 5.8 测试：命令、目录结构
- [x] 5.9 配置参考：环境变量清单
- [x] 5.10 README 头部时间戳

## 6. 最终验证

- [x] 6.1 全量 pytest
- [x] 6.4 人工 review

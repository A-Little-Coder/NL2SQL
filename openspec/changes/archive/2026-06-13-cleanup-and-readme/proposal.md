## Why

当前项目经过多轮迭代开发，积累了以下问题：
- **冗余代码**：部分函数/类在重构后被取代但未删除（如旧版 IR 召回实现、早期 schema_selection 逻辑），未被引用的 import 和工具函数堆积。
- **冗余测试**：测试目录中存在覆盖已被取代代码的过时测试用例，以及与被测代码不一致的 mock/stub。
- **缺少完整 README**：项目目前没有根目录 README.md，新成员或外部贡献者无法快速了解项目架构、安装步骤、模块职责和开发流程。

这些问题不影响运行时正确性，但损害可维护性和项目形象。本次 change 旨在系统性地清理技术债务，并建立一份完整的项目文档。

## What Changes

- **代码清理（不含运行时行为变更）**
  - 识别并删除未被引用的 import、死变量、空函数、注释掉的代码块
  - 删除被新实现完全取代的旧模块/旧类/旧函数（如被 LangGraph 子图取代的早期编排逻辑）
  - 删除覆盖已删除代码或与被测代码不一致的测试用例
  - 规范化测试目录结构，移除重复测试
- **README.md 撰写**
  - 在项目根目录创建完整的 README.md，覆盖：项目简介、架构总览、各模块职责、安装与配置、API 使用方式、LangSmith 监控、LangGraph Studio 调试、开发指南、测试说明
- **不做（保留现状）**
  - 不动 `requirements.txt` 的依赖声明（仅清理 import 语句本身）
  - 不重构 API 或数据库 schema
  - 不改变任何运行时行为

## Capabilities

### New Capabilities
- `code-cleanup`: 定义代码清理的范围、规则和验收标准，覆盖死代码删除、冗余 import 清理、测试用例裁剪
- `project-documentation`: 定义项目 README.md 的内容结构和覆盖范围，确保文档完整且与代码一致

### Modified Capabilities
<!-- 无现有 spec 的需求变更 -->

## Impact

- **代码清理**：涉及 `src/` 下各子模块（preprocessing、retrieval、schema_selection、sql_generation、execution、decision、memory、graph、api）和 `tests/` 及 `utils/`。清理后文件行数减少，但无功能变更。
- **README.md**：新增根文档，不影响任何代码。
- **测试**：测试用例减少，测试代码覆盖率的"数值"可能因删除针对已删除代码的用例而略微下降，但实际有效覆盖率不变。
- **工作量评估**：代码清理约 60%，README 撰写约 40%。
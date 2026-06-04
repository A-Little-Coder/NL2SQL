## Context

NL2SQL 项目已进入实现中后期，tasks.md 作为 OpenSpec 的核心进度追踪文件，与实际代码存在多处不一致：

- 已实现的功能未标记为完成（如 §2 预处理模块多个任务、§3 IR 改进）
- 部分路径描述与实际不符（tasks.md 中写 `scripts/`，实际代码在 `src/preprocessing/`）
- preprocessing spec 部分描述仍基于旧设计（每库独立 collection），与已实施的全局单 collection 方案不符
- 部分基础设施任务（`tavily-python` 依赖、`.gitignore` 规则、`user_memory` 目录）尚未创建

本 change 专注于修正这些不一致，不引入新的功能变更。

## Goals / Non-Goals

**Goals:**
- tasks.md 标记状态与实际代码同步
- preprocessing spec 更新以匹配实际实现
- 缺失的基础设施项补全（依赖、目录、.gitignore）

**Non-Goals:**
- 新增任何功能
- 修改已有代码的业务逻辑
- 添加新测试用例

## Decisions

| # | 决策 | 理由 |
|---|------|------|
| 1 | 只标记「已完全实现」的任务为完成 | §2.1-2.2, 2.5-2.7 代码已存在且可用；§3.7-3.8 已在 `retrieve_schema()` 和 `retrieve_values()` 中实现 |
| 2 | 不修改实际代码路径 | `scripts/` 描述错误只改 tasks.md 描述，不移动已有代码文件 |
| 3 | preprocessing spec 采用 delta 更新 | 保留原 spec 不变，新增 MODIFIED Requirements 覆盖全局单 collection 和路径变更 |
| 4 | `.env.example` 不存在则创建 | 当前无 `.env.example` 文件，按 proposal 要求创建 |

## Risks / Trade-offs

- **标记偏差**：部分任务可能只实现了核心逻辑但缺少边缘情况处理。解决方案：只标记「有可用代码且通过测试」的任务为完成。
- **Spec 同步后时效性**：spec 更新后需确保后续变更引用正确版本。解决方案：archive 本 change 后 tasks.md 和 spec 保持同步。

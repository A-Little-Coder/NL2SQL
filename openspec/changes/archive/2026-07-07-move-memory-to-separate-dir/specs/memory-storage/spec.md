## ADDED Requirements

### Requirement: 记忆数据独立目录存储
系统 SHALL 将所有运行时记忆数据存储在与预处理数据分离的目录下。

#### Scenario: 记忆数据写入 memory/ 目录
- **WHEN** 系统运行时写入会话历史、用户记忆或向量化历史 query
- **THEN** 数据 SHALL 写入 `memory/` 目录下的对应子目录，而非 `data/` 目录

#### Scenario: 记忆目录不可写时降级
- **WHEN** `MEMORY_DIR` 指向的目录无法写入
- **THEN** 系统 SHALL 记录错误日志并以安全降级方式继续运行（记忆功能暂不可用）

### Requirement: 记忆目录路径可配置
系统 SHALL 通过 `.env` 文件中的 `MEMORY_DIR` 环境变量配置记忆目录路径。

#### Scenario: 读取 MEMORY_DIR 配置
- **WHEN** 系统启动时读取 `.env` 文件
- **THEN** 系统 SHALL 使用 `MEMORY_DIR` 的值作为记忆数据根目录

#### Scenario: MEMORY_DIR 缺省值
- **WHEN** `.env` 文件不存在或 `MEMORY_DIR` 未设置
- **THEN** 系统 SHALL 默认使用 `./memory`（项目根下的 memory/ 目录）

### Requirement: 记忆数据迁移机制
系统 SHALL 提供一次性迁移脚本，将现有记忆数据从 `data/` 迁移到 `memory/`。

#### Scenario: 完整迁移流程
- **WHEN** 运行迁移脚本
- **THEN** 脚本 SHALL 将 `data/sessions/` 移动到 `memory/sessions/`
- **THEN** 脚本 SHALL 将 `data/user_memory/` 移动到 `memory/user_memory/`
- **THEN** 脚本 SHALL 将 `data/session_memory_v2/` 移动到 `memory/session_memory_v2/`
- **THEN** 脚本 SHALL 从 `data/preprocessed/chroma/` 读取 `nl2sql_session_queries` collection 并写入 `memory/chroma/`
- **THEN** 脚本 SHALL 删除旧 Chroma 中的 `nl2sql_session_queries` collection
- **THEN** 脚本 SHALL 提示用户手动删除旧目录 `data/sessions/`、`data/user_memory/`、`data/session_memory_v2/`

#### Scenario: 源数据不存在时的安全降级
- **WHEN** 迁移时某个源目录或 collection 不存在
- **THEN** 脚本 SHALL 跳过该项并记录警告日志，不中断整体迁移

### Requirement: 记忆目录结构
系统 SHALL 按以下目录结构组织记忆数据：

```
memory/
├── sessions/{user_id}/{session_id}.json               ← 会话记忆 v1
├── user_memory/{user_id}.json                          ← 用户长期记忆
├── session_memory_v2/{user_id}/{session_id}.json       ← 会话记忆 v2 JSON
└── chroma/                                             ← Chroma 向量库
    └── nl2sql_session_queries (collection)             ← 历史 query 向量
```

#### Scenario: 目录自动创建
- **WHEN** 首次写入记忆数据时
- **THEN** 系统 SHALL 自动创建对应的子目录（包括 `memory/sessions/`、`memory/user_memory/`、`memory/session_memory_v2/`、`memory/chroma/`）
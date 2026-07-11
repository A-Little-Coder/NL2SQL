## ADDED Requirements

#### Scenario: 处理包含混合数据类型的 BIRD-SQL 数据库
给定一个来自 BIRD-SQL 数据集的 SQLite 数据库，包含字符串、数字和日期时间类型的列
当预处理模块处理此数据库时
则它应该为所有包含唯一值的字符串列生成 LSH 索引
并且它应该为所有表和元数据生成 BGE-M3 嵌入向量
并且它应该将索引存储到配置的持久化存储中

#### Scenario: 高效处理大型数据库
给定一个包含超过 10 万行数据的数据库表
当预处理模块处理此表时
则它应该使用分批处理来避免内存溢出
并且它应该在 5 分钟内完成处理
并且它应该保持索引质量（LSH 召回率 > 0.95）

#### Scenario: 优雅地跳过损坏的数据库
给定一个无法打开的损坏 SQLite 数据库文件
当预处理模块尝试处理此文件时
则它应该记录错误并跳过此数据库
并且它应该继续处理其他有效的数据库
并且它应该在最终摘要中报告失败情况

#### Scenario: 支持增量更新
给定一个之前已处理过但已添加新表的数据库
当预处理模块以增量模式运行时
则它应该检测变化并仅处理新增/修改的表
并且它应该保留未变表的现有索引
并且它应该更新元数据以反映新状态

#### Scenario: 构建列级 Schema 向量索引（全局单 collection，全小写 document）
给定一个 BIRD-SQL 数据库目录（如 `data/california_schools/`）
当离线脚本执行 schema 向量索引构建时
则所有数据库的列向量应存放在**同一个** ChromaDB collection 中，命名为 `nl2sql_columns`
并且它应该遍历数据库所有表的所有列
并且对每列生成一条记录，包含：
  - id: `"{db_id}.{table_name}.{original_column_name}"`
  - document: 三段式格式 `{table_name} | {original_column_name} | {desc}`，全小写
  - desc 优先级：column_description → value_description → column_name（首个非空者）
  - embedding: BGE-M3 dense 向量（1024 维）
  - metadata: 完整列上下文（database、table_name、original_column_name、column_name、data_type、column_description、value_description、data_format、is_primary_key、is_foreign_key、references、sample_values），其中 `database` 字段用于区分不同数据库
并且 sample_values 应以 `"|"` 分隔字符串保存（不进入 embed 文本）
并且查询时通过 `where_filter={"database": db_id}` 实现隔离
并且 ChromaDB 持久化目录应为 `data/preprocessed/chroma/`（全局统一，不再按库分散）

#### Scenario: document 全小写保证 N-gram 匹配归一化
给定一个列其 `column_description` 为 "average scores in Reading"
当构建该列的 embed 文档时
则文档文本应为全小写：`satscores | avgscrread | average scores in reading`
并且 "score" 的 3-gram 能匹配文档中 "scores" 的 3-gram
使得 N-gram 投票精排时字面匹配不受大小写干扰

#### Scenario: 离线 Embedding 全流程使用本地 CPU BGE-M3
给定本地环境无 GPU
当执行 schema 向量索引构建时
则它应该加载 BGE-M3 模型（FlagEmbedding 库）使用 CPU 推理
并且不应调用任何远程 embedding API
并且对单个数据库的全部列（典型 100-500 列）应在 5 分钟内完成 embedding
并且对全部 11 个 BIRD 库的累计构建时间应 ≤ 15 分钟
并且生成的索引可在断网环境下被加载使用

#### Scenario: 支持强制重建索引
给定一个已建过 schema 向量索引的数据库
当构建脚本以 `--force-rebuild` 参数运行时
则它应该删除已存在的 chroma collection 目录
并且重新执行完整的 embed 与持久化流程
并且不带该参数时应跳过已建好的索引（幂等）
并且本期不要求基于 sqlite 文件 mtime 的自动过期检测

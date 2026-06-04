## MODIFIED Requirements

### Requirement: 构建列级 Schema 向量索引（全局单 collection）

**变更原因**：实际实现已改为全局单 collection 方案（`nl2sql_columns`），而非每库独立 collection。原 spec 描述与代码不一致。

**原内容**：
> 给定一个 BIRD-SQL 数据库目录（如 `data/california_schools/`）
> 则它应该为该数据库创建一个 ChromaDB collection，命名为 `columns_{db_id}`
> 并且 ChromaDB 持久化目录应为 `data/{db_id}/preprocessed/chroma/`

**更新后内容**：

给定一个 BIRD-SQL 数据库目录（如 `data/california_schools/`）
当离线脚本执行 schema 向量索引构建时
则所有数据库的列向量应存放在**同一个** ChromaDB collection 中，命名为 `nl2sql_columns`
并且每列记录应包含：
  - id: `"{db_id}.{table_name}.{original_column_name}"`
  - document: 按决策19顺序拼接的文本（含末尾 column_name boost）
  - embedding: BGE-M3 dense 向量（1024 维）
  - metadata: 完整列上下文，含 `database` 字段用于区分归属
并且 `metadata.database` 字段用于区分不同数据库的列
并且查询时通过 `where_filter={"database": db_id}` 实现隔离
并且 ChromaDB 持久化目录应为 `data/preprocessed/chroma/`（全局统一，不再按库分散）

#### Scenario: 通过 metadata.database 字段隔离多库检索
- **WHEN** 构建脚本为 `california_schools` 和 `frpm` 两个数据库构建索引
- **THEN** 它们共享同一个 `nl2sql_columns` collection
- **AND** `california_schools` 的列 metadata.database 为 `"california_schools"`
- **AND** 查询时指定 `where_filter={"database": "california_schools"}` 只会返回该库的列

### Requirement: 支持强制重建索引（全局范围）

**变更原因**：实际实现中 `force_rebuild` 作用于全局 collection，而非单库。原 spec 描述的"幂等跳过"行为仍在，但强制重建的粒度不同。

**原内容**：
> 则它应该删除已存在的 chroma collection 目录
> 并且重新执行完整的 embed 与持久化流程

**更新后内容**：

给定一个已建过 schema 向量索引的数据库
当构建脚本以 `--force-rebuild` 参数运行时
则它应该清空全局 `nl2sql_columns` collection
并且重新为所有数据库执行完整的 embed 与持久化流程
并且不带该参数时应跳过已建好的索引（幂等，通过 ChromaDB 的 id 去重）
并且本期不要求基于 sqlite 文件 mtime 的自动过期检测

#### Scenario: force_rebuild 清空所有库的索引
- **WHEN** 构建脚本以 `--force-rebuild` 参数运行
- **THEN** 全局 `nl2sql_columns` collection 被清空
- **AND** 所有数据库的列被重新构建

#### Scenario: 幂等追加（不指定 force_rebuild）
- **WHEN** 构建脚本不带 `--force-rebuild` 运行
- **THEN** 新的列文档被追加到 collection 中
- **AND** 已存在的 id 对应的记录被覆盖（upsert 语义）
- **AND** 其他库的已有记录不受影响

### Requirement: 离线构建脚本位置

**变更原因**：实际脚本位置为 `src/preprocessing/build_schema_index.py` 而非 `scripts/`。

原 spec 未指定脚本路径。实际实现中：
- 构建脚本：`src/preprocessing/build_schema_index.py`
- LSH 构建脚本：`src/preprocessing/build_lsh_index.py`

## ADDED Requirements

### Requirement: LSH 离线索引构建

系统应提供独立的 LSH 离线索引构建脚本，与 schema 向量索引构建并行运行。

#### Scenario: 为单个数据库构建 LSH 索引
- **WHEN** 运行 `build_lsh_for_db("california_schools")`
- **THEN** 为该数据库的所有字符串列生成 MinHash 签名
- **AND** 索引持久化到 `data/preprocessed/lsh/{db_id}/` 目录

#### Scenario: 全量构建所有 LSH 索引
- **WHEN** 运行 `build_all_lsh()`
- **THEN** 遍历 `data/` 下所有数据库目录
- **AND** 逐个构建 LSH 索引

### Requirement: 列级文档包含值描述和数据格式字段

SchemaColumnDocGenerator 应支持所有决策19规定的字段，包括 `value_description` 和 `data_format`。

#### Scenario: 完整字段文档生成
- **WHEN** 调用 `format_column_document()` 并传入所有字段
- **THEN** 输出文档包含 `{table_name} {original_column_name} {column_name} {data_type} {column_description} {value_description} {data_format} {column_name}`
- **AND** 末尾 column_name 出现两次（boost）

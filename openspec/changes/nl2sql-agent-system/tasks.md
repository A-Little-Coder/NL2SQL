## 1. 环境设置和依赖安装

- [x] 1.1 配置 conda 虚拟环境 NL2SQL 并安装核心依赖包
- [x] 1.2 下载并验证 BIRD-SQL 数据集的完整性和数据库格式
- [x] 1.3 配置 Qwen API 和 LangSmith 环境变量
- [x] 1.4 安装和测试 BGE-M3 embedding 模型
- [x] 1.5 在 `requirements.txt` 中新增依赖：`tavily-python>=0.3.0`、`langgraph>=0.2.0`
- [x] 1.6 在 `.env.example` 中新增 `TAVILY_API_KEY=` 配置项
- [x] 1.7 创建 `data/user_memory/` 目录并加入 `.gitkeep`，在 `.gitignore` 中排除 `data/user_memory/*.json`

## 2. 预处理模块开发

- [x] 2.1 实现数据库连接管理器（支持 SQLite 和 MySQL）
- [x] 2.2 开发 LSH 索引生成器，为所有字段唯一值创建哈希索引
- [x] 2.3 实现 schema 向量化模块，使用 BGE-M3 生成表/列描述嵌入
- [x] 2.4 集成 ChromaDB 向量存储，保存和检索 schema 嵌入
- [x] 2.5 实现 schema 列文档生成器：
  - 按 决策 19 的三段式格式拼装 `document` 文本：`{table_name} | {original_column_name} | {desc}`（全小写）
  - desc 优先级：`column_description` → `value_description` → `column_name`
  - metadata 包含 database/table_name/original_column_name/column_name/data_type/column_description/value_description/data_format/is_primary_key/is_foreign_key/references/sample_values
  - sample_values 用 `"|"` 分隔成字符串（适配 Chroma metadata 限制）
- [x] 2.6 实现离线索引构建脚本 `src/preprocessing/build_schema_index.py`（实际路径为 `src/preprocessing/`，原 tasks.md 中 `scripts/` 为误写）：
  - 命令行参数：`--db_id`（支持 `all`）、`--force-rebuild`、`--data_dir`
  - 加载 BGE-M3（本地 CPU）批量 embed
  - 写入 `data/preprocessed/chroma/nl2sql_columns/`（全局单 collection，通过 metadata.database 区分）
  - document 全小写，与决策 19 一致
- [x] 2.7 为 schema 列文档生成器和构建脚本编写单元测试（含三段式格式断言、metadata 完整性、全小写验证）

## 3. 信息检索 (IR) 模块开发

- [x] 3.1 实现关键词提取功能，使用 LLM 配合 few-shot 示例
- [x] 3.2 开发 LSH 值检索组件，支持近似匹配
- [x] 3.3 实现语义 schema 检索，基于 ChromaDB 向量相似性
- [x] 3.4 集成两阶段检索策略，合并 LSH 和语义检索结果
- [x] 3.5 修改 `InformationRetriever.retrieve()` 返回值，附带：
  - 每个候选的 `similarity_score`
  - LSH 命中数量统计
  - 向量检索 top_k 分数列表
- [x] 3.6 更新 `tests/retrieval/test_information_retriever.py` 验证新字段
- [x] 3.7 改写 `retrieve_schema()` 为「每 keyword 独立检索 + 合并去重」：
  - 输入 keywords 列表，对每个 keyword 调一次 `vector_store.query(top_k=5)`
  - 合并时同 `table.column` 多次命中取最高 score
  - 按 score 降序返回；新增可配参数 `column_top_k_per_keyword`
- [x] 3.8 升级 `retrieve_values()` 为「LSH + 语义精排」两阶段：
  - LSH top N 候选 → BGE-M3 embedding 计算 (keyword, value) 余弦相似度
  - 过滤 < `value_semantic_threshold`（默认 0.6）
  - 返回结果同时附带 LSH Jaccard 和 embedding 分数
- [x] 3.9 修改 `tests/e2e_live.py` 接入真实 schema 检索：
  - 启动时调用 `prepare_schema_index(db_dir)`（未建则报错或提示运行 build 脚本）
  - IR 步骤展示每 keyword 的列检索 top 5 与分数
  - 移除"全表全列直接塞入 context"的旧逻辑

## 3.10 IR 召回优化（决策 18/19/21/25 改造）

- [x] 3.10.1 重写关键词提取 prompt（决策 21）：
  - 输出格式改为 `{"keywords": [{"phrase": str, "zh_synonyms": [str], "en_synonyms": [str]}]}`
  - 四向扩写：中文同义词 + 英文翻译 + 英文同义词 + 中文翻译
  - 短语保留规则：名词前的描述性定语、量词不单独切分（如"各科score"作为一个短语）
  - 所有输出全小写
- [x] 3.10.2 修改 `extract_keywords()` 解析新格式，返回结构化的关键词 + 同义词列表
- [x] 3.10.3 修改 `extract_keywords()` 返回结构化分组（决策 18 改造）：
  - 返回 `List[KeywordGroup]`，每个 KeywordGroup 包含 `phrase`（原生关键词）和 `terms`（phrase + zh_synonyms + en_synonyms 扁平列表）
  - 下游 `retrieve_schema()` 按分组独立召回
- [x] 3.10.4 修改 `retrieve_schema()` 为按关键词分组独立召回（决策 18/25 改造）：
  - 输入改为 `List[KeywordGroup]`
  - 每个关键词组：组内所有 terms 各自查 top50 → 取并集 → 组内 n-gram 投票 → 取 top10
  - 返回 `Dict[str, List[RetrievedItem]]`，key 为原生关键词 phrase，value 为该组的 top10 列
- [x] 3.10.5 修改 `retrieve()` 流程适配分组返回结构：
  - 跨组汇总所有列，重复列去重但标注来源关键词
  - `RetrievedContext` 中保留关键词→列的映射关系（新增 `keyword_columns_map` 字段）
- [x] 3.10.6 修改 `schema_doc_generator.py` 的 `format_column_document()` 输出全小写（决策 19）
- [x] 3.10.7 重建 ChromaDB 索引（document 全小写 + 三段式格式）
- [x] 3.10.8 更新 `tests/retrieval/test_information_retriever.py`：
  - 测试 KeywordGroup 结构化返回
  - 测试分组独立召回（组内投票、组间不干扰）
  - 测试 N-gram 投票函数
  - 测试综合排序逻辑（vector 权重 ≤ 0.2）
  - 测试全小写 document 匹配

## 3.11 表关联图（Schema Relationship Graph，决策 26）

- [x] 3.11.1 创建 `src/preprocessing/schema_graph_builder.py`，实现 `SchemaGraphBuilder` 类：
  - `__init__(db_connector, vector_store, llm_client, value_overlap_threshold, top_similar_pairs, sample_size)`
  - `build(db_id) -> dict` 返回完整图结构
  - `save(graph, output_path)` / `load(path)` 持久化
  - `extract_join_paths(graph, table_names)` 运行时 BFS 路径提取
  - `format_join_paths_for_prompt(join_paths)` 格式化 Prompt
- [x] 3.11.2 实现 Stage 1：显式 FK 提取
  - PRAGMA foreign_key_list → `explicit_fk` 类型 edge
  - 多 FK 指向同一表合并为一个 edge，多个 join_keys
- [x] 3.11.3 实现 Stage 2：向量相似度匹配 + 值命中率检测
  - 从 ChromaDB 获取列 embedding，计算跨表列余弦相似度
  - 取 top 3 相似列对作为候选
  - 值命中率检测：从表 A 列取 N 个 DISTINCT 值，检查在表 B 列中的匹配数
  - 命中率 = 匹配数 / 样本数，超过阈值（默认 0.5）确认为 join_key
  - 支持多连接键，类型兼容性检查
  - 生成 `vector_similarity` 类型 edge
- [x] 3.11.4 实现 Stage 3：LLM 辅助
  - 对孤立表对，用 LLM 判断 JOIN 关系
  - 生成 `llm_inferred` 类型 edge
- [x] 3.11.5 实现 JSON 邻接表存储（`data/preprocessed/schema_graphs/{db_id}.json`）
- [x] 3.11.6 实现构建脚本 `src/preprocessing/build_schema_graphs.py`
- [x] 3.11.7 IR 模块集成：`RetrievedContext` 新增 `join_paths` 和 `join_paths_text` 字段
  - `retrieve()` 中 `_inject_join_paths()` 自动加载图并注入 JOIN 条件
- [x] 3.11.8 编写单元测试 `tests/preprocessing/test_schema_graph_builder.py`（30 个用例）
- [x] 3.11.9 修复值验证方法：Jaccard → 命中率（决策 26 更新）
  - 原方案：双方各取 20 个值算 Jaccard 交集/并集，大表下交集概率极低
  - 新方案：从表 A 列取 N 个 DISTINCT 值，检查在表 B 列中有多少能匹配
  - 命中率 = 匹配数 / 样本数，阈值默认 0.5
  - SQL 实现：`SELECT COUNT(*) FROM (SELECT DISTINCT col FROM table_a LIMIT N) WHERE col IN (SELECT DISTINCT col FROM table_b)`
  - 更新 `_verify_value_overlap()` 方法
- [x] 3.11.10 增强 `extract_join_paths()` 支持桥接表识别：
  - 返回值改为 `{"edges": [...], "bridge_tables": [str, ...]}`
  - 桥接表 = 路径中出现的表 - IR 召回的表集合
  - 多条路径时取最短路径
  - 更新 `format_join_paths_for_prompt()` 展示桥接表
- [x] 3.11.11 `_inject_join_paths()` 自动补充桥接表的 M-Schema：
  - 从向量库查询桥接表的所有列，加入 `RetrievedContext.columns`
  - 补充桥接表到 `RetrievedContext.tables`
  - 更新相关单元测试
- [x] 3.11.12 Stage 3 LLM 推断的 join_keys 也需要命中率检测验证：
  - LLM 可能幻觉出不存在的关联，需要用命中率检测过滤假阳性
  - 在 `_llm_infer_join` 返回后，对 join_keys 调用 `_verify_value_overlap` 验证
  - 只有通过命中率检测的 join_keys 才写入边

## 3.12 预处理增量更新（决策 27）

- [x] 3.12.1 实现 `src/preprocessing/manifest.py`：
  - `Manifest` 类，负责 Manifest 文件的加载、保存、diff 计算
  - `load(path) -> ManifestData`：加载 manifest.json
  - `save(path, manifest_data)`：保存 manifest.json（原子写入）
  - `compute_diff(old_manifest, current_schema) -> DiffResult`：对比 manifest 与当前 DB schema，输出 added_tables / removed_tables / modified_tables（modified 包含 added_columns / removed_columns / changed_columns）
  - `build_manifest_from_schema(db_id, all_schemas) -> ManifestDBEntry`：从 schema 构建单库 manifest 条目
  - Manifest 存储路径：`data/preprocessed/manifest.json`
- [x] 3.12.2 修改 `DatabaseManifest`，将 `build_time` 拆为三个独立字段：
  - `schema_index_build_time: Optional[str]`：Schema Index 构建时间，null 表示未构建
  - `schema_graph_build_time: Optional[str]`：Schema Graph 构建时间，null 表示未构建
  - `lsh_index_build_time: Optional[str]`：LSH Index 构建时间，null 表示未构建
  - 修改 `Manifest.load()` / `save()` / `build_manifest_from_schema()` 适配新字段
  - 兼容旧格式：`build_time` 存在时自动填充三个字段
- [x] 3.12.3 修改全量构建脚本，各自只写自己的 build_time：
  - `build_schema_index_for_db()` 完成后只更新 `schema_index_build_time`
  - `build_schema_graphs()` 每库成功后只更新 `schema_graph_build_time`
  - `build_lsh_for_db()` 成功后只更新 `lsh_index_build_time`
  - 保证增量更新可以区分各模块的构建进度
- [x] 3.12.4 实现 Schema Index 增量更新方法：
  - `incremental_update_schema_index(db_id, diff, vector_store, vectorizer)`：
    - `schema_index_build_time == null` → 全量构建
    - 新增表：upsert 该表所有列向量
    - 删除表：ChromaDB `delete(where={"database": db_id, "table_name": table})`
    - 新增列：upsert 单列向量
    - 删除列：ChromaDB `delete(ids=[f"{db_id}.{table}.{col}"])`
    - 修改列：upsert 覆盖（id 相同自动覆盖）
- [x] 3.12.5 实现 Schema Graph 增量更新方法（含依赖检查）：
  - `incremental_update_schema_graph(db_id, diff, graph, builder)`：
    - 依赖检查：`schema_index_build_time == null` → 跳过 + 警告"Schema Index 未构建"
    - `schema_graph_build_time == null` → 全量构建
    - 上游级联：Schema Index 有变更 → 即使 Graph diff 为空也需要重新处理
    - 新增表 T：添加 node T → T vs 所有表做 Stage 1/2/3
    - 删除表 T：删除 node T + 所有 from/to 含 T 的边
    - 表 T 新增列：只对 T 与未连接表做 Stage 2 匹配（只拿新增列向量去 ChromaDB 匹配）
    - 表 T 删除列：检查该列是否参与 join_key → 移除该 join_key；边 ≥1 个 join_key 则保留
    - 表 T 修改列类型：重验证受影响 join_key 的类型兼容性
- [x] 3.12.6 实现 LSH Index 增量更新方法：
  - `incremental_update_lsh_index(db_id, diff, db_directory)`：
    - `lsh_index_build_time == null` → 全量构建
    - 新增表 T：加载已有 LSH + minhashes → 计算新表 MinHash → insert → 保存
    - 删除表 T：加载 LSH + minhashes → remove 该表所有 key → 保存
    - 修改表 T：remove 旧 key + insert 新 key → 保存
    - 采用表级重建策略（非行级修改）
- [x] 3.12.7 实现统一增量更新入口 `src/preprocessing/incremental_updater.py`：
  - `IncrementalUpdater` 类，`__init__(data_dir, hit_rate_threshold, top_similar_pairs, sample_size, llm_client)`
  - `update(db_id) -> UpdateReport`：对单个库执行增量更新
  - `update_all() -> List[UpdateReport]`：扫描所有库，只更新有 diff 的库
  - 内部流程：加载 Manifest → 读取当前 DB schema → compute_diff → 按 ①Schema Index ②Schema Graph ③LSH 顺序执行 → 依赖检查 + 上游级联信号传递 → 任何一步失败则停止 → 各模块完成后更新各自的 build_time
  - `check_updates(db_id=None, data_dir=None)`：仅检测 diff，不执行更新
  - `UpdateReport` 包含：db_id、diff、各模块的 status/变更统计
- [x] 3.12.8 编写 `tests/preprocessing/test_incremental_updater.py`：
  - 测试 Manifest 的 load/save/compute_diff
  - 测试三模块独立 build_time：各构建脚本只写自己的时间戳
  - 测试 Schema Index 增量：新增表、删除表、新增列、删除列、修改列
  - 测试 Schema Graph 依赖检查：Schema Index 未构建时跳过 + 警告
  - 测试 Schema Graph 级联触发：Schema Index 有变更时 Graph 重新处理
  - 测试 Schema Graph 增量：新增表、删除表、新增列、删除列
  - 测试 LSH Index 增量：新增表、删除表、修改表（表级重建）
  - 测试统一入口：diff 为空时跳过、某步失败时停止并保持一致性
  - 测试全量构建后自动写入 Manifest


## 4. Schema 选择器 (SS) 模块开发

- [x] 4.1 实现 M-schema 格式转换器，将检索结果转为 M-schema
- [x] 4.2 开发列相关性评估功能，使用 LLM 配合 few-shot 过滤列
- [x] 4.3 集成 CHESS 的 M-schema 生成逻辑

## 5. 候选 SQL 生成器 (CG) 模块开发

- [x] 5.1 实现命名实体识别和掩码功能，使用 nltk 工具
- [x] 5.2 开发 few-shot 示例选择器，基于查询骨架相似性
- [x] 5.3 实现多 SQL 生成器，最多生成 5 个候选 SQL
- [x] 5.4 集成 sqlglot 安全验证，过滤危险操作和语法错误 (基础版已实现)

## 5.5 可回答性检查（Answerability Check，决策 23）

- [x] 5.5.1 创建 `src/verification/__init__.py`
- [x] 5.5.2 实现 `src/verification/answerability.py` 的 `AnswerabilityChecker` 类：
  - `__init__(llm_client, strictness="loose")`
  - `check(user_query, mschema, ir_context) -> AnswerabilityResult`
  - `AnswerabilityResult` 包含：answerable(true/false/uncertain)、confidence、reason、missing_info、granularity_match
  - Prompt 模板：宽松原则，只要有合理可能性就放行，只有明确缺少关键实体/粒度严重不匹配才拦截
  - Prompt 中包含完整的 MSchema 信息（表名、列名、数据类型、description、sample_values、PK/FK）
- [x] 5.5.3 编写 `tests/verification/test_answerability.py`：
  - 测试明确不可回答（如"每个学生的成绩"但只有学校级别数据）→ false
  - 测试可能可回答（uncertain）→ 放行
  - 测试明确可回答 → true
  - 测试 reason 和 missing_info 字段正确返回

## 5.6 结果可信度验证（Result Verification，决策 24）

- [x] 5.6.1 实现 `src/verification/result_verifier.py` 的 `ResultVerifier` 类：
  - `__init__(llm_client, strictness="strict")`
  - `verify(user_query, selected_sql, result_sample, mschema) -> VerificationResult`
  - `VerificationResult` 包含：trustworthy(true/false)、reason、granularity_match、semantic_alignment
  - Prompt 模板：严格原则，检查粒度匹配、维度覆盖、硬凑检测三个维度
  - `result_sample` 为 SQL 执行结果的列名 + 前 5 行
- [x] 5.6.2 编写 `tests/verification/test_result_verifier.py`：
  - 测试答非所问（SQL 查学校但问学生）→ 不可信
  - 测试正常对齐 → 可信
  - 测试粒度不匹配 → 不可信
  - 测试 reason 字段给出有用的拒答原因

## 6. 执行引擎开发

- [x] 6.1 开发安全 SQL 执行器，支持 EXPLAIN 验证
- [x] 6.2 实现错误捕获和结构化错误信息提取
- [x] 6.3 集成错误修正循环，支持最多 2 次重试

## 7. Self-Consistency 决策模块开发

- [x] 7.1 实现结果一致性检测，比较多个 SQL 执行结果
- [x] 7.2 开发投票决策逻辑：多数一致选最快，全不同调用 LLM
- [x] 7.3 集成 LLM 最终决策功能，提供候选 SQL 和执行上下文
- [x] 7.4 在 `SelfConsistencyDecision.decide()` 中集成 `ResultVerifier`：
  - 选定最终 SQL 后调用 `verifier.verify()`
  - 不可信时覆盖 `decision_result` 为拒答，填入 `rejection_reason`
  - 可信时正常返回结果

## 8. 监控和用户界面集成

- [ ] 8.1 集成 LangSmith 全流程监控，记录 trace 链路
- [ ] 8.2 开发 Terminal 交互式界面，支持流式输出 (框架已建立)
- [ ] 8.3 实现思考过程可视化，显示各阶段执行状态

## 9. UserMemory 模块（capability: user-memory，决策 29 扩展）

- [x] 9.1 创建 `src/memory/__init__.py`
- [x] 9.2 实现 `src/memory/storage.py`：JSON 读写 + 跨平台文件锁（`fcntl` / `msvcrt`）
- [x] 9.3 实现 `src/memory/user_memory.py` 的 `UserMemory` 类，方法：
  - `__init__(user_id, base_dir="data/user_memory")`
  - `load() -> dict`
  - `save() -> None`（原子写入：tmp → rename）
  - `get_term_preference(term: str) -> Optional[dict]`
  - `record_term_preference(term: str, resolved_to: str, confidence: float, source: str = "user_taught")`
  - `append_clarification(history_entry: dict)`
  - `get_domain_context() -> dict`（返回完整领域信息，含 industry/department/focus_areas）
  - `record_table_usage(table_name: str)`（自动学习常用表）
  - `get_frequently_used_tables(top_k: int = 5) -> List[str]`
  - `record_metric_definition(name, description, sql_pattern, source, confidence)`（双轨：auto_learned + user_taught）
  - `get_metric_definitions(min_confidence: float = 0.7) -> List[dict]`
  - `update_query_preference(key: str, value: str)`（自动学习查询偏好）
  - `get_query_preferences() -> dict`
  - `update_domain_context(**kwargs)`
- [x] 9.4 编写 `tests/memory/test_user_memory.py`：覆盖创建/读写/并发锁/原子写入 + 新增 6 维记忆的读写

## 10. WebSearchEnricher（Tavily 集成）

- [ ] 10.1 实现 `src/clarification/web_search.py`：
  - `TavilySearcher` 类，封装 `tavily-python` 客户端
  - `search(query: str) -> List[dict]` 带 10s 超时、3 条结果上限
  - 会话级 LRU 缓存装饰器（基于 `query` 字符串去重）
- [ ] 10.2 实现「术语是否为未知领域」的 LLM 判断函数
- [ ] 10.3 编写 `tests/clarification/test_web_search.py`：Mock Tavily 响应 + 缓存命中测试

## 11. TriggerDetector

- [ ] 11.1 实现 `src/clarification/trigger.py` 的 `TriggerDetector` 类
  - 输入：IR 结果、用户查询、UserMemory 实例、配置开关
  - 输出：`TriggerResult(triggered: bool, type: Literal["A","B","C","D",None], reason: str)`
- [ ] 11.2 实现四类触发判断：
  - A: 召回为空
  - B: LLM 语义不匹配判断（带 Prompt 模板）
  - C: 相似度阈值（向量 < 0.4 或 LSH < 0.3）
  - D: 与 UserMemory.term_preferences 冲突
- [ ] 11.3 添加配置项：`config/clarification.yaml` 控制各触发器开关
- [ ] 11.4 编写 `tests/clarification/test_trigger.py`：四类触发各覆盖正负样本

## 12. QuestionGenerator

- [ ] 12.1 实现 `src/clarification/question_generator.py`
  - `generate(context: ClarificationContext) -> str`
  - `ClarificationContext` 包含：原始查询、触发类型、IR 候选、用户记忆摘要、Web 搜索结果（可选）
- [ ] 12.2 设计 Prompt 模板（中文，含粗/细粒度示例）
- [ ] 12.3 编写 `tests/clarification/test_question_generator.py`：固定 LLM 响应下生成质量验证

## 13. UserDialog（LangGraph 中断）

- [ ] 13.1 实现 `src/clarification/dialog.py`
  - `DialogManager.ask(question: str) -> str` 包装 LangGraph `interrupt`
  - 拒答关键词识别：["不知道","跳过","算了","skip","不清楚","随便"]
  - 计数器管理 + 5 次硬上限
- [ ] 13.2 编写 `tests/clarification/test_dialog.py`：模拟 interrupt/resume 流程

## 14. ClarificationAgent 主类

- [ ] 14.1 实现 `src/clarification/agent.py` 的 `ClarificationAgent`：
  - 构造 LangGraph 子图：TriggerDetector → (条件分支) → WebSearchEnricher → QuestionGenerator → UserDialog → MemoryWriter
  - 暴露 `run(state: NL2SQLState) -> NL2SQLState` 接口供主图调用
- [ ] 14.2 实现 MemoryWriter 步骤：本次查询结束后将所有 `clarification_history` 条目写回 UserMemory
- [ ] 14.3 编写 `tests/clarification/test_agent_integration.py`：完整子图 Mock 集成测试

## 15. NL2SQLState 与主图集成

- [x] 15.1 在主 state 定义中新增字段：`clarification_count`、`clarification_history`、`clarified_keywords`、`web_search_cache`、`user_id`、`answerability_result`、`result_verification`、`rejection_reason`
- [x] 15.2 在 LangGraph 主图中插入 `clarification` 节点（IR 之后、SS 之前）
- [x] 15.3 配置条件边：`clarification_done == True` → SS；否则循环回 `clarification`
- [x] 15.4 在 LangGraph 主图中插入 `answerability_check` 节点（SS 之后、CG 之前）
- [x] 15.5 配置条件边：`answerable != "false"` → CG；否则 → END（拒答 + 原因）
- [x] 15.6 修改 `decision` 节点集成结果验证：不可信时写入 `rejection_reason` 并跳转 END

## 16. 文档与示例

- [ ] 16.1 在 `docs/` 下新增 `clarification_agent.md` 说明使用方法与触发条件
- [ ] 16.2 在 `examples/` 下新增 `clarification_demo.py` 演示完整反问流程
- [ ] 16.3 更新 `README.md` 提及反问能力与 Tavily API Key 配置

## 17. 测试和验证

- [ ] 17.1 编写单元测试，覆盖各核心模块
- [ ] 17.2 进行端到端集成测试，使用 BIRD-SQL 测试集
- [ ] 17.3 验证安全性，确保不会执行危险 SQL 操作
- [ ] 17.4 性能测试和优化，确保响应时间可接受
- [ ] 17.5 所有新增/修改单元测试通过
- [ ] 17.6 端到端 Demo：使用「查一下苹果的销售额」触发 B 类反问，澄清后 SQL 正确生成
- [ ] 17.7 UserMemory 持久化验证：重启会话后历史偏好仍可命中
- [ ] 17.8 拒答场景验证：连续 5 次反问后自动退出，主流程继续

## 18. LangGraph 全流程编排（贯穿性改造）

依据 决策 22：本服务全程基于 LangGraph 编排，主图 + 各 Agent 子图。

- [x] 18.1 定义主图 State：`src/graph/state.py` 内 `NL2SQLState` (TypedDict)
  - 覆盖字段：`user_query`、`keywords`、`retrieved_context`、`selected_schema`、`sql_candidates`、`execution_results`、`final_decision`、`clarification_*`、`user_id`、`error`、`trace_log`
- [x] 18.2 实现主图 `src/graph/main_graph.py`：
  - 节点：`ir → clarification → ss → cg → execution → decision → END`
  - 条件边：clarification_done / execution 全部失败兜底 / 无候选 SQL 兜底
  - 提供 `build_main_graph(config) -> CompiledGraph` 工厂函数
- [x] 18.3 IR Agent 子图化：
  - 子图节点：`extract_keywords → (retrieve_values || retrieve_schema) → enhance_with_schema`
  - 保持 `InformationRetrieval.retrieve()` 公开签名（内部改为 `self.graph.invoke(...)`）
- [x] 18.4 SS Agent 子图化：
  - 子图节点：`to_mschema → evaluate_relevance → filter_columns`
  - 保持 `SchemaSelector.select()` 公开签名
- [x] 18.5 CG Agent 子图化：
  - 子图节点：`extract_entities → mask_query → select_few_shot → llm_generate → safety_validate`
  - 保持 `SQLGenerator.generate()` 公开签名
- [x] 18.6 Execution Agent 子图化：
  - 子图节点：`explain → execute → (条件分支) llm_fix → execute`（循环 max_retries 次）
  - 保持 `SQLFixLoop.run()` 公开签名
- [x] 18.7 Decision Agent 子图化：
  - 子图节点：`group_by_result → find_majority → (条件分支) select_fastest | llm_final_decision`
  - 保持 `SelfConsistencyDecision.decide()` 公开签名
- [x] 18.8 每个 Agent 类暴露 `build_graph() -> CompiledGraph` 方法（约定 API）
- [ ] 18.9 集成 LangSmith 追踪：主图与所有子图节点自动产生 trace 链路（呼应 §8.1）
- [x] 18.10 编写 `tests/graph/test_main_graph.py`：
  - 端到端 Mock 测试整条主图能跑通
  - 验证状态在节点间正确流动
  - 验证条件边（如全部 SQL 失败时主图正确终止）
- [x] 18.11 编写 `tests/graph/test_subgraphs.py`：每个 Agent 子图独立可调用 + 状态契约验证
- [x] 18.12 修改 `tests/e2e_live.py` 使用 `build_main_graph()` 代替手写流程（当前先外部流程展示，未来完整接入子图条件边）

## 19. 会话记忆模块（决策 28）

- [x] 19.1 实现 `src/memory/session_memory.py` 的 `SessionMemory` 类：
- [x] 19.2 实现 `src/memory/session_manager.py` 的 `SessionManager` 类：
- [x] 19.3 编写 `tests/memory/test_session_memory.py`：覆盖创建/加载/追加轮次/Prompt格式化/上下文摘要/持久化验证
- [x] 19.4 编写 `tests/memory/test_session_manager.py`：覆盖创建/列出/删除/LRU缓存/用户隔离

## 20. 问数服务 API（决策 31）

- [x] 20.1 新增依赖：`fastapi>=0.100.0`、`uvicorn>=0.20.0`、`sse-starlette>=1.0.0`
- [x] 20.2 实现 `src/api/schemas.py`：Pydantic 请求/响应模型
  - `QueryRequest(query: str, session_id: str, user_id: str)`
  - `SSEEvent(type: str, **kwargs)`
  - `SessionSummary(session_id, created_at, updated_at, status, turn_count)`
  - `UserMemoryResponse(...)`
- [x] 20.3 实现 `src/api/deps.py`：依赖注入
  - `get_session_manager()` → 单例 SessionManager
  - `get_user_memory(user_id)` → 懒加载 UserMemory（LRU 缓存）
  - `get_graph()` → 预编译的 LangGraph 主图
- [x] 20.4 实现 `src/api/stream.py`：SSE 事件生成器
  - `generate_sse_events(state_stream)` → AsyncGenerator
  - 监听各阶段状态变更，实时推送 SSE 事件
  - 处理 cache_check / stage / clarification / result / error / done 事件类型
- [x] 20.5 实现 `src/api/routes/query.py`：核心查询接口
  - `POST /api/v1/query`：SSE 流式响应
  - 加载或创建 SessionMemory → 注入 conversation_history 到 state → 调用 graph.invoke → 提取结果 → 更新 SessionMemory 和 UserMemory
  - 流式：每个节点执行后推送阶段事件
- [x] 20.6 实现 `src/api/routes/session.py`：会话管理接口
  - `GET /api/v1/sessions/{user_id}`：列出用户会话
  - `GET /api/v1/sessions/{session_id}/history`：获取对话历史
  - `DELETE /api/v1/sessions/{session_id}`：删除会话
- [x] 20.7 实现 `src/api/routes/user.py`：用户记忆接口
  - `GET /api/v1/users/{user_id}/memory`：获取完整用户记忆
  - `GET /api/v1/users/{user_id}/metrics`：获取指标定义
- [x] 20.8 实现 `src/api/app.py`：FastAPI 应用 + 生命周期
  - `lifespan()` 中一次性加载所有组件
  - 注册路由
  - 健康检查 `GET /api/v1/health`
- [x] 20.9 编写 `tests/api/test_query.py`：Mock 组件测试 SSE 流式响应
- [x] 20.10 编写 `tests/api/test_session.py`：测试会话 CRUD 接口
- [x] 20.11 编写 `tests/api/test_user.py`：测试用户记忆查询接口

## 21. 历史命中检测 + 记忆自动学习（决策 30 + 29）

- [x] 21.1 实现 `src/memory/history_cache.py` 的 `HistoryCache` 类：
  - `__init__(llm_client, min_confidence=0.8)`
  - `check(query: str, session_memory: SessionMemory, user_memory: UserMemory) -> CacheResult`
  - `CacheResult(hit: bool, cached_sql: Optional[str], source: Optional[str], confidence: float)`
  - 内部调 LLM 判断当前查询是否与历史等价或可用已知指标回答
  - 安全边界：confidence < min_confidence → 不复用；时间相关的 follow-up → 不复用
- [x] 21.2 实现 `src/memory/memory_updater.py` 的 `MemoryUpdater` 类：
  - `update(user_memory: UserMemory, session_memory: SessionMemory, state: NL2SQLState) -> None`
  - 自动学习逻辑：
    - 从 `final_sql` 提取表名 → `record_table_usage()`
    - 检测简单聚合 SQL → 调 LLM 提取指标定义 → `record_metric_definition(source="auto_learned")`
    - 统计查询偏好频率 → `update_query_preference()`
  - 写入 clarification_history（如有反问）
  - 更新 session_memory 的 context_summary
- [x] 21.3 修改 `src/graph/state.py`，新增字段：
  - `conversation_history: List[Dict[str, Any]]`（会话历史，由 API 层注入）
  - `cache_hit: bool`
  - `cached_sql: Optional[str]`
  - `cache_source: Optional[str]`
  - `cache_confidence: float`
- [x] 21.4 修改 `src/graph/main_graph.py`，新增节点和条件边：
  - 新增 `history_cache` 节点（START 之后，IR 之前）
  - 条件边：`cache_hit == True` → execution；否则 → ir
  - 新增 `memory_update` 节点（decision 之后，END 之前）
  - 调整流程：`START → history_cache → ir → ... → decision → memory_update → END`
- [x] 21.5 修改 IR 节点，支持读取 `conversation_history` 辅助 follow-up 理解
  - 在关键词提取 Prompt 中注入会话历史
  - 支持"那去年的呢"类 follow-up 的关键词补全
- [x] 21.6 修改 CG 节点，注入用户偏好和指标定义
  - 从 `state` 中读取用户记忆（API 层注入）
  - 注入 `query_preferences`（默认时间/排序/limit）
  - 注入 `metric_definitions`（min_confidence >= 0.8）
- [x] 21.7 编写 `tests/memory/test_history_cache.py`：覆盖命中/未命中/低置信度/时间相关 follow-up
- [x] 21.8 编写 `tests/memory/test_memory_updater.py`：覆盖常用表学习/指标定义学习/查询偏好学习/会话上下文更新

## 22. API 多数据库分池重构（决策 49）

依据 决策 49：将 API 从「启动时绑定单一 db」改为「全局单例 + DbContext LRU 池」，支持单进程服务多数据库。

- [x] 22.1 新增 `src/api/db_pool.py`：DbContext + DbContextPool
  - `class DbContext`：持有 `db_id` / `connector` / `lsh_indexer` / `retriever` / `selector` / `executor` / `fix_loop` / `graph` / `refcount`
  - `DbContext.close()`：disconnect connector，清理资源
  - `class DbContextPool`：
    - `__init__(max_size, globals_)`：保存全局组件引用、初始化 OrderedDict 缓存
    - `acquire(db_id) -> DbContext`：命中 LRU move_to_end；未命中触发 `_build`；refcount += 1
    - `release(db_id)`：refcount -= 1
    - `_build(db_id)`：按 `tests/e2e_live_with_memory.py` 第 282-366 行的逻辑构造组件（复用全局 BGE / VectorStore / LLM / Generator / Decider / Answerability / HistoryCache / MemoryUpdater）
    - `_evict_if_needed()`：池满时遍历找 refcount=0 的最久未用 ctx 淘汰；全部在用则跳过（允许短暂超 max）
    - `close_all()`：shutdown 时关闭所有 ctx
  - 用 `threading.RLock()` 保护 OrderedDict 操作
- [x] 22.2 重写 `src/api/deps.py`
  - 删除现有 `init_components` 写死方案
  - 新增模块级全局变量：`_bge_vectorizer` / `_vector_store` / `_llm_client` / `_generator` / `_decider` / `_answerability` / `_history_cache` / `_memory_updater` / `_session_manager` / `_db_pool`
  - 新增 `init_globals()`：从 `.env` 读 `BGE_M3_MODEL_PATH` / `QWEN_MODEL` / `DB_POOL_MAX_SIZE`，按依赖顺序构造所有全局组件 + 实例化 `_db_pool`
  - 新增 FastAPI 依赖项：`get_db_pool()` / `get_globals()` / `get_db_context(db_id)`（前两个返回单例，第三个内部 `pool.acquire`）
  - 保留：`get_session_manager()` / `get_user_memory(user_id)`
- [x] 22.3 修改 `src/api/schemas.py`
  - `QueryRequest` 新增 `db_id: str = Field(..., description="数据库 id")`
  - 字段验证：`db_id` 非空字符串
- [x] 22.4 修改 `src/api/routes/query.py`
  - 改写 `query_endpoint`：从 body 取 `db_id` → `pool.acquire(db_id)` 拿 `db_ctx` → 用 `db_ctx.graph` 替代 `Depends(get_graph)`
  - `try/finally` 包裹整段 SSE 流，`finally` 中调 `pool.release(db_id)`
  - history_cache / memory_updater 仍走全局单例（不在 DbContext 内）
- [x] 22.5 新增 `src/api/routes/databases.py`
  - `GET /api/v1/databases`：扫描 `data/` 目录返回所有可用 db_id（复用 `find_bird_databases` 逻辑）
  - `GET /api/v1/databases/{db_id}/tables`：触发 `pool.acquire(db_id)` 后调 `connector.get_tables()`；finally release
- [x] 22.6 修改 `src/api/routes/session.py`
  - 新增 `POST /api/v1/sessions`：body `{user_id, db_id?}` → 调 `session_manager.create_session(user_id)` 返回 `{session_id}`
  - 现有 list/get/delete 接口不变
- [x] 22.7 修改 `src/api/app.py` 的 `lifespan`
  - startup：调 `init_globals()`，并根据 `--db_id` 启动参数（通过 env 或 app.state 传入）做可选 warm-up
  - shutdown：调 `_db_pool.close_all()`
- [x] 22.8 重写 `run_api.py`
  - 删除 `bootstrap(db_id)` 全量初始化逻辑
  - `--db_id` 参数改为可选（默认 None），传值时通过 `app.state.warmup_db_id` 注入 lifespan
  - `--port` / `--host` / `--reload` 保留
  - 不再传 `workers` 参数（始终单 worker）
- [x] 22.9 修改 `.env.example`，新增配置项
  - `DB_POOL_MAX_SIZE=2`（DbContext 池最大容量）
  - 文档说明 BGE / VectorStore / LLM 等组件为全局单例，无需配置
- [x] 22.10 编写 `tests/api/test_db_pool.py`
  - 测试 LRU 淘汰顺序
  - 测试 refcount > 0 时跳过淘汰（允许短暂超 max）
  - 测试并发 acquire/release 不破坏 OrderedDict
  - 测试 `close_all()` 释放所有 connector
- [x] 22.11 编写 `tests/api/test_query_multi_db.py`
  - 测试同一 session_id 切换 db_id 不会污染会话历史
  - 测试 SSE 流式响应正确（mock graph）
  - 测试缺少 `db_id` 时返回 422
- [x] 22.12 编写 `tests/api/test_databases.py`
  - 测试 `GET /databases` 返回所有 db_id
  - 测试 `GET /databases/{db_id}/tables` 返回表清单
  - 测试不存在的 db_id 返回 404

## 23. API 真流式 + LLM 思考链推送（决策 50）

依据 决策 50：把伪流式 SSE 改为「边跑边吐 + LLM 思考链推送 + 心跳」三位一体。
**注：不推送 LLM 正文 token**（业务全为 JSON 模式，token 是 JSON 片段不可读），仅推送 qwen3 的 `reasoning_content`（自然语言思考链）。

- [x] 23.1 新增 `src/api/streaming.py`：StreamEmitter + contextvars
  - `class StreamEmitter`：持有 `asyncio.Queue` + `loop`，`emit(event_type, data)` 用 `loop.call_soon_threadsafe(queue.put_nowait, evt)`
  - `current_emitter: ContextVar[Optional[StreamEmitter]]`
  - `current_node: ContextVar[Optional[str]]`
  - 工具函数：`emit_safe(event_type, data)` — 当前无 emitter 时静默不发
- [x] 23.2 改造 `utils/llm_client.py`
  - 新增 `chat_stream(messages, on_thinking=None, response_format=None, **kwargs) -> str`
    - 内部 `stream=True`；遍历 chunks
    - **正文 `delta.content` 仅累积到 full_text**，不回调（避免推 JSON 片段）
    - **思考链 `delta.reasoning_content` 实时回调** `on_thinking`
    - `extra_body={"enable_thinking": True}` 默认开（可通过参数关闭）
    - 末尾返回累积的 full_text
  - 改造 `chat_json(messages, ...)`：检查 `current_emitter.get()`；有则走 `chat_stream`，把 `on_thinking` 绑定到 `emitter.emit("llm_thinking", {"node": current_node, "text": c})`；拿到 full_text 后 `json.loads` + 正则兜底
  - 改造 `chat(messages, ...)`：同上但不解析 JSON
  - 保留旧函数签名不破坏现有调用方
- [x] 23.3 改造 `src/graph/main_graph.py` 节点工厂
  - 每个节点（history_cache / ir / clarification / ss / answerability_check / cg / execution / decision / memory_update）进入时 `current_node.set("ir")` + `emit_safe("stage", {node, status: "started"})`
  - 退出时 `emit_safe("stage", {node, status: "done", <关键字段>})` + `current_node.set(None)`
  - 关键节点 emit 业务事件：
    - IR 节点：`emit_safe("keywords", ...)` + `emit_safe("schema_recall", ...)`
    - 可回答性：`emit_safe("answerability", {answerable, reason, confidence})`
    - CG 节点：`emit_safe("sql_candidates", {candidates: [{id, sql}]})`
    - Execution：每条候选执行完 `emit_safe("execution", {candidate_id, success, rows})`
    - Decision：`emit_safe("final_decision", {selected_id, reason})`
- [x] 23.4 重写 `src/api/routes/query.py:event_stream`
  - 用 `asyncio.Queue` + sentinel 桥接同步 graph 与 async SSE
  - `run_graph` 在线程中执行：进入时 `current_emitter.set(emitter)`，出来时 `reset` + `queue.put_nowait(sentinel)`
  - 用 `contextvars.copy_context().run(run_graph)` 保证 contextvar 跨线程传递
  - 主循环 `await asyncio.wait_for(queue.get(), timeout=15.0)`；TimeoutError 时 yield `: heartbeat\n\n`
  - 异常捕获：把 emitter 异常包成 `error` 事件
  - 最终输出 `done` 事件，状态码已在响应头返回 200
- [x] 23.5 修改 `src/api/routes/query.py` 客户端 timeout 文档
  - 在 docstring 中说明客户端应使用 `httpx.Timeout(connect=10, read=None, write=10, pool=10)` 或依赖心跳
- [x] 23.6 更新 `tests/api_client_demo.py`
  - 改 `timeout=180` 为 `httpx.Timeout(connect=10, read=None, write=10, pool=10)`
  - 按事件类型分别打印：`stage` 显示节点切换；`llm_thinking` 用前缀 `[思考]` 累计到同一行（按 node 分组）；`answerability` / `keywords` / `sql_candidates` 等业务事件结构化输出；`result` 单独成行
- [x] 23.7 更新 `.env.example`
  - 新增 `LLM_ENABLE_THINKING=true`（控制 qwen3 思考链开关）
  - 新增 `SSE_HEARTBEAT_INTERVAL=15`（心跳间隔秒）
- [x] 23.8 编写 `tests/api/test_streaming.py`
  - 测试 `StreamEmitter` 跨线程 emit 不丢事件
  - 测试 `current_emitter` contextvar 在 `copy_context().run()` 下正确传递
  - 测试 `chat_stream` 收到 reasoning_content chunks 后 `on_thinking` 被调用
  - 测试 `chat_stream` 收到 content chunks 时**不**触发任何回调（仅累积）
  - 测试 emitter=None 时 `chat_json` 走旧阻塞路径（向后兼容）
  - 测试非 qwen3 模型（无 reasoning_content 字段）时不抛异常、不推送 llm_thinking
- [x] 23.9 编写 `tests/api/test_query_stream.py`
  - mock graph 让其 yield 多个 update，验证 SSE 事件按时间顺序到达（非"先攒后吐"）
  - 测试 15 秒无事件时心跳行 `: heartbeat\n\n` 被发出
  - 测试 graph 抛异常时 `error` 事件被推送 + `done` 兜底
  - 测试 SSE 流中**不出现** `llm_chunk` 事件类型
- [x] 23.10 烟测：跑一次真实查询验证
  - `python run_api.py --db_id california_schools`
  - `python tests/api_client_demo.py`
  - 观察：节点级 stage 实时打印；qwen3 思考链以自然中文片段流入；不出现 JSON 片段；首字节延迟 < 5 秒；业务事件（keywords / answerability / sql_candidates 等）结构化到达


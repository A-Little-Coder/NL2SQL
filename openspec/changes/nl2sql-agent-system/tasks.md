## 1. 环境设置和依赖安装

- [ ] 1.1 配置 conda 虚拟环境 NL2SQL 并安装核心依赖包
- [ ] 1.2 下载并验证 BIRD-SQL 数据集的完整性和数据库格式
- [ ] 1.3 配置 Qwen API 和 LangSmith 环境变量
- [ ] 1.4 安装和测试 BGE-M3 embedding 模型
- [ ] 1.5 在 `requirements.txt` 中新增依赖：`tavily-python>=0.3.0`、`langgraph>=0.2.0`
- [ ] 1.6 在 `.env.example` 中新增 `TAVILY_API_KEY=` 配置项
- [ ] 1.7 创建 `data/user_memory/` 目录并加入 `.gitkeep`，在 `.gitignore` 中排除 `data/user_memory/*.json`

## 2. 预处理模块开发

- [ ] 2.1 实现数据库连接管理器（支持 SQLite 和 MySQL）
- [ ] 2.2 开发 LSH 索引生成器，为所有字段唯一值创建哈希索引
- [x] 2.3 实现 schema 向量化模块，使用 BGE-M3 生成表/列描述嵌入
- [x] 2.4 集成 ChromaDB 向量存储，保存和检索 schema 嵌入
- [ ] 2.5 实现 schema 列文档生成器：
  - 按 决策 19 的顺序拼装 `document` 文本（含 `column_name` 末尾 boost）
  - metadata 包含 database/table_name/original_column_name/column_name/data_type/column_description/value_description/data_format/is_primary_key/is_foreign_key/references/sample_values
  - sample_values 用 `"|"` 分隔成字符串（适配 Chroma metadata 限制）
- [ ] 2.6 实现离线索引构建脚本 `scripts/build_schema_index.py`：
  - 命令行参数：`--db_id`（支持 `all`）、`--force-rebuild`、`--data_dir`
  - 加载 BGE-M3（本地 CPU）批量 embed
  - 写入 `data/preprocessed/chroma/nl2sql_columns/`（全局单 collection，通过 metadata.database 区分）
- [ ] 2.7 为 schema 列文档生成器和构建脚本编写单元测试（含 boost 文本断言、metadata 完整性）

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
- [ ] 3.7 改写 `retrieve_schema()` 为「每 keyword 独立检索 + 合并去重」：
  - 输入 keywords 列表，对每个 keyword 调一次 `vector_store.query(top_k=5)`
  - 合并时同 `table.column` 多次命中取最高 score
  - 按 score 降序返回；新增可配参数 `column_top_k_per_keyword`
- [ ] 3.8 升级 `retrieve_values()` 为「LSH + 语义精排」两阶段：
  - LSH top N 候选 → BGE-M3 embedding 计算 (keyword, value) 余弦相似度
  - 过滤 < `value_semantic_threshold`（默认 0.6）
  - 返回结果同时附带 LSH Jaccard 和 embedding 分数
- [ ] 3.9 修改 `tests/test_e2e_live.py` 接入真实 schema 检索：
  - 启动时调用 `prepare_schema_index(db_dir)`（未建则报错或提示运行 build 脚本）
  - IR 步骤展示每 keyword 的列检索 top 5 与分数
  - 移除"全表全列直接塞入 context"的旧逻辑


## 4. Schema 选择器 (SS) 模块开发

- [x] 4.1 实现 M-schema 格式转换器，将检索结果转为 M-schema
- [x] 4.2 开发列相关性评估功能，使用 LLM 配合 few-shot 过滤列
- [x] 4.3 集成 CHESS 的 M-schema 生成逻辑

## 5. 候选 SQL 生成器 (CG) 模块开发

- [x] 5.1 实现命名实体识别和掩码功能，使用 nltk 工具
- [x] 5.2 开发 few-shot 示例选择器，基于查询骨架相似性
- [x] 5.3 实现多 SQL 生成器，最多生成 5 个候选 SQL
- [x] 5.4 集成 sqlglot 安全验证，过滤危险操作和语法错误 (基础版已实现)

## 6. 执行引擎开发

- [x] 6.1 开发安全 SQL 执行器，支持 EXPLAIN 验证
- [x] 6.2 实现错误捕获和结构化错误信息提取
- [x] 6.3 集成错误修正循环，支持最多 2 次重试

## 7. Self-Consistency 决策模块开发

- [x] 7.1 实现结果一致性检测，比较多个 SQL 执行结果
- [x] 7.2 开发投票决策逻辑：多数一致选最快，全不同调用 LLM
- [x] 7.3 集成 LLM 最终决策功能，提供候选 SQL 和执行上下文

## 8. 监控和用户界面集成

- [ ] 8.1 集成 LangSmith 全流程监控，记录 trace 链路
- [ ] 8.2 开发 Terminal 交互式界面，支持流式输出 (框架已建立)
- [ ] 8.3 实现思考过程可视化，显示各阶段执行状态

## 9. UserMemory 模块（capability: user-memory）

- [ ] 9.1 创建 `src/memory/__init__.py`
- [ ] 9.2 实现 `src/memory/storage.py`：JSON 读写 + 跨平台文件锁（`fcntl` / `msvcrt`）
- [ ] 9.3 实现 `src/memory/user_memory.py` 的 `UserMemory` 类，方法：
  - `__init__(user_id, role_tag=None, base_dir="data/user_memory")`
  - `load() -> dict`
  - `save() -> None`（原子写入：tmp → rename）
  - `get_term_preference(term: str) -> Optional[dict]`
  - `record_term_preference(term: str, resolved_to: str, confidence: float)`
  - `append_clarification(history_entry: dict)`
  - `get_domain_context() -> List[str]`
- [ ] 9.4 编写 `tests/memory/test_user_memory.py`：覆盖创建/读写/并发锁/原子写入

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

- [ ] 15.1 在主 state 定义中新增字段：`clarification_count`、`clarification_history`、`clarified_keywords`、`web_search_cache`、`user_id`
- [ ] 15.2 在 LangGraph 主图中插入 `clarification` 节点（IR 之后、SS 之前）
- [ ] 15.3 配置条件边：`clarification_done == True` → SS；否则循环回 `clarification`

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
- [x] 18.12 修改 `tests/test_e2e_live.py` 使用 `build_main_graph()` 代替手写流程（当前先外部流程展示，未来完整接入子图条件边）


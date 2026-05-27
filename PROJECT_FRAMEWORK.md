# NL2SQL Agent 项目框架

## 项目结构

```
NL2SQL/
├── src/                          # 源代码目录
│   ├── __init__.py              # 包初始化
│   ├── main.py                  # 主入口文件
│   │
│   ├── preprocessing/           # 预处理模块
│   │   ├── __init__.py
│   │   ├── database_connector.py    # 数据库连接管理器【待实现】
│   │   ├── lsh_index.py             # LSH 索引生成器【待实现】
│   │   ├── schema_vectorizer.py     # Schema 向量化器【待实现】
│   │   └── vector_store.py          # 向量存储管理器【待实现】
│   │
│   ├── retrieval/               # 信息检索模块
│   │   ├── __init__.py
│   │   └── information_retrieval.py # IR 主类【待实现】
│   │
│   ├── schema_selection/        # Schema 选择器模块
│   │   ├── __init__.py
│   │   └── schema_selector.py   # M-schema 转换和列过滤【待实现】
│   │
│   ├── sql_generation/          # SQL 生成器模块
│   │   ├── __init__.py
│   │   ├── sql_generator.py     # SQL 多候选生成器【待实现】
│   │   └── sql_validator.py     # SQL 安全验证器【已实现基础版】
│   │
│   ├── execution/               # 执行引擎模块
│   │   ├── __init__.py
│   │   ├── executor.py          # SQL 执行器【待实现】
│   │   └── error_handler.py     # 错误处理器【待实现】
│   │
│   ├── decision/                # 决策模块
│   │   ├── __init__.py
│   │   └── self_consistency.py  # Self-Consistency 决策器【待实现】
│   │
│   └── monitor/                 # 监控和用户界面模块
│       ├── __init__.py
│       ├── langsmith_monitor.py # LangSmith 监控器【待实现】
│       └── terminal_interface.py# Terminal 交互界面【部分实现】
│
├── data/                        # 数据目录（存放数据库文件）
├── tests/                       # 测试目录
├── openspec/                    # OpenSpec 变更提案
│   └── changes/nl2sql-agent-system/
├── requirements.txt             # Python 依赖
└── download_data.py             # BIRD-SQL 数据集下载脚本
```

## 开发顺序建议

### 第一阶段：基础设施
1. **环境设置** (任务 1.1-1.4)
   - 创建 conda 环境：`conda create -n NL2SQL python=3.10`
   - 安装依赖：`pip install -r requirements.txt`
   - 配置环境变量（Qwen API, LangSmith）

2. **数据处理** (任务 2.1-2.4)
   - 运行 `python download_data.py` 下载数据集
   - 实现 `DatabaseConnector` 连接 SQLite 数据库
   - 实现 `LSHIndexer` 为字段值建立索引
   - 实现 `SchemaVectorizer` 使用 BGE-M3 生成嵌入
   - 实现 `VectorStoreManager` 使用 ChromaDB 存储向量

### 第二阶段：核心功能
3. **信息检索** (任务 3.1-3.4)
   - 实现关键词提取（LLM + few-shot）
   - 实现 LSH 值检索
   - 实现语义 schema 检索
   - 集成两阶段检索策略

4. **Schema 选择** (任务 4.1-4.3)
   - 实现 M-schema 格式转换
   - 实现列相关性评估
   - 集成 CHESS 的 M-schema 逻辑

5. **SQL 生成** (任务 5.1-5.4)
   - 实现命名实体识别（nltk）
   - 实现 few-shot 示例选择
   - 实现多 SQL 生成
   - 集成 sqlglot 安全验证

### 第三阶段：执行和决策
6. **执行引擎** (任务 6.1-6.3)
   - 实现 SQLExecutor（支持 EXPLAIN）
   - 实现 ErrorHandler（结构化错误）
   - 实现错误修正循环

7. **决策模块** (任务 7.1-7.3)
   - 实现结果一致性检测
   - 实现投票决策逻辑
   - 集成 LLM 最终决策

### 第四阶段：整合
8. **监控和 UI** (任务 8.1-8.3)
   - 集成 LangSmith
   - 完善 Terminal 界面
   - 实现思考过程可视化

9. **测试** (任务 9.1-9.4)
   - 编写单元测试
   - 端到端集成测试
   - 安全性验证
   - 性能优化

## 关键类说明

### DatabaseConnector (src/preprocessing/database_connector.py)
负责数据库连接，提供统一接口访问 SQLite/MySQL。
- `get_tables()` - 获取所有表名
- `get_table_schema(table_name)` - 获取表的 schema
- `execute_query(sql)` - 执行查询

### LSHIndexer (src/preprocessing/lsh_index.py)
为字段值创建 LSH 索引，用于快速近似匹配。
- `build_index(values)` - 构建索引
- `query(value, top_k)` - 查询相似值

### SchemaVectorizer (src/preprocessing/schema_vectorizer.py)
使用 BGE-M3 模型将 schema 元素转换为向量。
- `embed_texts(texts)` - 批量文本向量化
- `embed_schema(schema_info)` - schema 向量化

### InformationRetrieval (src/retrieval/information_retrieval.py)
两阶段检索：LSH + 语义检索。
- `extract_keywords(query)` - 关键词提取
- `retrieve(query)` - 完整检索流程

### SQLGenerator (src/sql_generation/sql_generator.py)
生成多个候选 SQL。
- `generate(schema, query)` - 生成最多 5 个候选

### SelfConsistencyDecision (src/decision/self_consistency.py)
投票决策：多数一致选最快，全不同调用 LLM。
- `decide(candidates, query)` - 最终决策

## 依赖关系图

```
main.py (NL2SQLAgent)
    │
    ├─→ preprocessing/
    │     ├─ DatabaseConnector → sqlite3/mysql-connector
    │     ├─ LSHIndexer → datasketch (待添加)
    │     ├─ SchemaVectorizer → FlagEmbedding
    │     └─ VectorStoreManager → chromadb
    │
    ├─→ retrieval/
    │     └─ InformationRetrieval → 依赖 preprocessing
    │
    ├─→ schema_selection/
    │     └─ SchemaSelector → 依赖 retrieval
    │
    ├─→ sql_generation/
    │     ├─ SQLGenerator → 依赖 schema_selection, dashscope
    │     └─ SQLValidator → sqlglot
    │
    ├─→ execution/
    │     ├─ SQLExecutor → 依赖 preprocessing.DatabaseConnector
    │     └─ ErrorHandler
    │
    ├─→ decision/
    │     └─ SelfConsistencyDecision → 依赖 execution
    │
    └─→ monitor/
          ├─ LangSmithMonitor → langsmith
          └─ TerminalInterface
```

## 下一步行动

1. 按照上述顺序逐个实现各模块
2. 参考 docs/需求文档.txt 中的详细需求
3. 参考 CHESS 项目的 Prompt 设计和实现细节
4. 参考 M-Schema 项目的格式定义
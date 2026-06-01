## ADDED Requirements

#### Scenario: 从模糊的自然语言查询中提取关键词
给定一个自然语言查询 "显示去年的销售额"
当 IR 模块使用 few-shot 示例处理此查询时
则它应该提取相关关键词如"销售额"、"去年"
并且它应该识别用于日期过滤的时间表达式
并且它应该在查询意图不明确时请求澄清

#### Scenario: 值检索采用 LSH 粗召回 + 语义向量精排两阶段
给定一个包含具体实体或字段值的关键词（如 "Hamilton"、"上海"）
当 IR 模块执行值检索时
则它应该先用 LSH（MinHash）召回每个关键词的 top N（默认 N=10）个候选值
并且它应该对 (keyword, 候选值) 对用 BGE-M3 计算 embedding 余弦相似度
并且它应该过滤掉 embedding 相似度 < `value_semantic_threshold`（默认 0.6）的候选
并且返回的每个值需附带 LSH Jaccard 分数和 embedding 分数两个维度
并且当 LSH 索引未加载时应优雅降级（跳过值检索，返回空列表）

#### Scenario: 表/列 schema 检索采用纯语义相似性，每个关键词取 top K
给定一个查询的关键词列表（如 ["销售额", "去年"]）
当 IR 模块执行 schema 检索时
则它应该只在「列级 collection」上进行检索（不单建表级 collection）
并且它应该对每个关键词独立调用一次向量库相似度查询（top K=5）
并且它应该合并所有关键词的查询结果
并且对同一 `table.column` 的多次命中应保留最高 score
并且最终结果按 score 降序排序返回
并且检索 query 使用纯关键词，不拼接原始 query 整句

#### Scenario: 表覆盖率通过列检索结果反推保证
给定列检索返回 N 个候选列
当 IR 模块构建 RetrievedContext 时
则它应该通过 `enhance_with_schema` 方法自动把每个列所属的表加入 `context.tables`
并且新增的表 score 取自原列的 score（保留排序信息）
并且不应丢失任何 LSH 值检索命中的表（值所属表也通过 enhance_with_schema 加入）

#### Scenario: 检索结果附带分数与元数据
给定 IR 模块完成检索
当 `InformationRetrieval.retrieve()` 返回 `RetrievedContext` 时
则每个 `RetrievedItem` 应该包含 `score` 字段（语义相似度或 LSH Jaccard）
并且 `RetrievedContext.lsh_hit_count` 应该是值检索后剩余值的总数
并且 `RetrievedContext.vector_top_scores` 应该是列检索 top-k 的 score 列表
并且 `RetrievedItem.metadata` 应该包含完整列上下文（data_type、is_primary_key、references 等）

#### Scenario: 处理训练数据中不存在的术语
给定一个包含训练数据中没有的领域特定术语的查询
当 IR 模块处理此查询时
则它应该回退到值的精确字符串匹配（LSH 字面 Jaccard）
并且它应该对 schema 元素使用更广泛的语义匹配
并且它应该在词汇不匹配的情况下保持合理的召回率

#### Scenario: 大型 schema 下的高效检索
给定一个包含 50+ 表和 500+ 列的数据库
当 IR 模块执行检索时
则它应该在 2 秒内完成（不含 LLM 关键词提取时间）
并且它应该返回 top-k 最相关的列（k=5/keyword，合并后预期 10-25 列）
并且单次 query 的总 embedding 调用次数应 ≤ N_keyword × (1 + N_LSH_top)
并且对相关元素保持精度 > 0.8

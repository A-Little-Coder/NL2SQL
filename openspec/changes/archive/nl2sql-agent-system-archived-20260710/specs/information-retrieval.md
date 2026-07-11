## ADDED Requirements

#### Scenario: 从自然语言查询中提取关键词（四向同义词扩写）
给定一个自然语言查询 "各个学校的各科score"
当 IR 模块使用 LLM 提取关键词时
则它应该保留名词前的描述性定语/量词为短语（如"各科score"不拆成"各科"+"score"）
并且它应该为每个短语输出中文同义词（如"各科成绩"、"每科分数"）
并且它应该为每个短语输出英文同义词/翻译（如"subject score"、"course score"）
并且所有输出应全小写
并且输出格式为 `{"keywords": [{"phrase": str, "zh_synonyms": [str], "en_synonyms": [str]}]}`

#### Scenario: 值检索采用 LSH 粗召回 + 语义向量精排两阶段
给定一个包含具体实体或字段值的关键词（如 "Hamilton"、"上海"）
当 IR 模块执行值检索时
则它应该先用 LSH（MinHash）召回每个关键词的 top N（默认 N=10）个候选值
并且它应该对 (keyword, 候选值) 对用 BGE-M3 计算 embedding 余弦相似度
并且它应该过滤掉 embedding 相似度 < `value_semantic_threshold`（默认 0.6）的候选
并且返回的每个值需附带 LSH Jaccard 分数和 embedding 分数两个维度
并且当 LSH 索引未加载时应优雅降级（跳过值检索，返回空列表）

#### Scenario: 表/列 schema 检索按关键词分组独立召回 + 组内 N-gram 投票精排
给定一个查询的关键词分组列表，每组含原生 phrase 和同义词扩写后的 terms
当 IR 模块执行 schema 检索时
则它应该按原生关键词分组，每组独立召回
并且组内对每个 term 独立调用一次向量库相似度查询（top K=50）
并且组内所有 term 的结果取并集（去重）
并且它应该在组内并集上执行 3-gram 子串匹配投票（只用本组的 terms，只拆解关键词不拆解 document）
并且投票得分为所有 term 的所有 n-gram 在 document 原文中的出现次数之和（如 "sch" 出现2次则贡献2分）
并且不对投票得分除以 terms 总数（分组独立召回无需跨组比较）
并且综合排序公式为 `final_score = vector_score × 0.2 + normalized_ngram_vote × 0.8`
并且向量相似度权重不超过 0.2
并且每组独立返回 top 10 列
并且不同关键词组之间的召回结果互不干扰

#### Scenario: 跨组汇总时重复列去重但标注来源
给定多个关键词组各自返回了 top 5 列
当 IR 模块汇总结果时
则重复列只保留一份 M-Schema
但标注该列被哪些关键词召回
并且 Prompt 中同时展示"关键词→列"映射关系和去重后的 M-Schema

#### Scenario: document 文本全小写保证 N-gram 匹配归一化
给定 ChromaDB 中的列文档
则每条 document 应为全小写格式：`{table_name} | {original_column_name} | {desc}`
并且 desc 优先级为 column_description → value_description → column_name
并且查询时的检索词也应全小写
使得 "score" 的 3-gram {"sco","cor","ore"} 能匹配 "scores" 的 3-gram {"sco","cor","ore","res"}

#### Scenario: 表覆盖率通过列检索结果反推保证
给定列检索返回 N 个候选列
当 IR 模块构建 RetrievedContext 时
则它应该通过 `enhance_with_schema` 方法自动把每个列所属的表加入 `context.tables`
并且新增的表 score 取自原列的 score（保留排序信息）
并且不应丢失任何 LSH 值检索命中的表（值所属表也通过 enhance_with_schema 加入）

#### Scenario: 检索结果附带分数与元数据
给定 IR 模块完成检索
当 `InformationRetrieval.retrieve()` 返回 `RetrievedContext` 时
则每个 `RetrievedItem` 应该包含 `score` 字段（综合排序分，含向量 + n-gram 投票成分）
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
则它应该在 5 秒内完成（不含 LLM 关键词提取时间）
并且向量粗召回 top_k=50 保证候选集足够大
并且 N-gram 投票精排应在线性时间内完成
并且对相关元素保持精度 > 0.8

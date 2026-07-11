## ADDED Requirements

#### Scenario: 可回答性检查 — 明确无法回答时拦截

给定用户查询"每个学生的各科平均分"和数据库中只有学校级别的 SAT 分数（无学生粒度数据）
当可回答性检查节点执行判断时
则它应该判定 answerable = "false"
并且它应该返回 reason 说明"该数据库没有学生粒度数据，只有学校级别的分数"
并且它应该将 rejection_reason 写入状态并终止流程
并且它不应该继续执行 SQL 生成和执行阶段

#### Scenario: 可回答性检查 — 不确定时放行

给定用户查询中包含一些不太明确的概念但数据库中有可能相关的字段
当可回答性检查节点执行判断时
则它应该判定 answerable = "uncertain"
并且它应该放行到 SQL 生成阶段
并且它不应该拦截不确定的查询

#### Scenario: 可回答性检查 — 明确可以回答时放行

给定用户查询"查一下北京的学校数量"和数据库中有 schools 表及 region 列
当可回答性检查节点执行判断时
则它应该判定 answerable = "true"
并且它应该正常继续到 SQL 生成阶段

#### Scenario: 可回答性检查 — 使用完整 MSchema 元数据

给定 SS 模块输出的 MSchema 包含表名、列名、数据类型、description、sample_values、PK/FK 关系
当可回答性检查节点构造 LLM Prompt 时
则它应该将完整的 MSchema 信息提供给 LLM
并且它应该将 IR 的 keywords、lsh_hit_count、vector_top_scores 作为辅助判断信息
并且 LLM 应基于这些元数据判断粒度是否匹配和字段是否覆盖用户需求

#### Scenario: 结果可信度验证 — 粒度不匹配时拒答

给定最终选定的 SQL 查询的是学校级别的数据
但用户问题要求学生级别的数据
当结果可信度验证执行时
则它应该判定 trustworthy = "false"
并且它应该返回 reason 说明"SQL 查询粒度为学校，但用户要求学生粒度，答非所问"
并且它应该将 rejection_reason 写入状态并终止流程

#### Scenario: 结果可信度验证 — 正常对齐时通过

给定最终选定的 SQL 查询的字段和粒度与用户问题一致
当结果可信度验证执行时
则它应该判定 trustworthy = "true"
并且它应该正常返回查询结果

#### Scenario: 结果可信度验证 — 硬凑检测

给定用户查询要求"学生姓名"但 SQL 返回的列是"School"
当结果可信度验证执行时
则它应该判定 trustworthy = "false"
并且它应该在 reason 中说明"查询请求学生姓名，但结果以学校名称替代，存在硬凑"

#### Scenario: 结果可信度验证 — 基于实际执行结果判断

给定 SQL 已成功执行并返回结果
当结果可信度验证构造 LLM Prompt 时
则它应该将 SQL 执行结果的列名和前 5 行样例提供给 LLM
并且它应该将原始 MSchema 作为对照提供给 LLM
并且它应该将用户原始问题提供给 LLM

#### Scenario: 拒答时返回有用原因

给定可回答性检查或结果验证判定不可回答/不可信
当流程因拒答终止时
则它应该在 rejection_reason 中返回 LLM 生成的详细原因
并且原因应包含缺失信息或粒度不匹配的具体说明
并且原因应以自然语言呈现，便于用户理解

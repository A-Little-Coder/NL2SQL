# ============================================================================
# Retrieval 模块的 Prompt 模板（OpenSpec migrate-llm-client-to-langchain）
# ============================================================================
# 集中管理 information_retrieval.py 中的 LLM prompt：
#   - KEYWORD_EXTRACTION_PROMPT       关键词抽取（含同义词扩写）
#   - KEYWORD_EXTRACTION_WITH_HISTORY 同上，但附加 follow-up 会话上下文
# ============================================================================

from langchain_core.prompts import ChatPromptTemplate


# 基础关键词抽取（无会话历史）
KEYWORD_EXTRACTION_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "你是数据库查询分析专家，只输出 JSON。"),
    ("user", """请从用户查询中提取用于数据库检索的关键词，并为每个关键词提供同义词扩写。

提取规则：
1. 提取可能与数据库字段名或值匹配的关键词
2. 名词前的描述性定语、量词不单独切分，保留为短语（如"各科成绩"不拆成"各科"+"成绩"）
3. 时间表达式保留原样（如"去年"、"2023年"）
4. 度量词保留（如"销售额"、"利润"）
5. 实体名称保留（如"苹果"、"北京"）
6. 不要提取常见停用词（如"显示"、"查询"、"找出"等动词）
7. 为每个关键词提供中文同义词和英文同义词/翻译
8. 所有输出全小写
9. 返回 JSON 格式：{{"keywords": [{{"phrase": "关键词", "zh_synonyms": ["同义词1"], "en_synonyms": ["synonym1"]}}]}}

示例：
输入："各个学校的各科score"
输出：{{"keywords": [{{"phrase": "学校", "zh_synonyms": ["学校", "院校"], "en_synonyms": ["school", "schools"]}}, {{"phrase": "各科score", "zh_synonyms": ["各科成绩", "每科分数"], "en_synonyms": ["subject score", "course score", "each subject score"]}}]}}

输入："显示去年北京地区的销售额"
输出：{{"keywords": [{{"phrase": "去年", "zh_synonyms": ["去年"], "en_synonyms": ["last year", "previous year"]}}, {{"phrase": "北京", "zh_synonyms": ["北京", "北京市"], "en_synonyms": ["beijing"]}}, {{"phrase": "销售额", "zh_synonyms": ["销售额", "营收"], "en_synonyms": ["sales", "revenue", "sales amount"]}}]}}

输入："找出销售额超过100万的客户"
输出：{{"keywords": [{{"phrase": "销售额", "zh_synonyms": ["销售额", "营收"], "en_synonyms": ["sales", "revenue", "sales amount"]}}, {{"phrase": "100万", "zh_synonyms": ["100万", "一百万"], "en_synonyms": ["1 million", "1000000"]}}, {{"phrase": "客户", "zh_synonyms": ["客户", "顾客"], "en_synonyms": ["customer", "client"]}}]}}

输入："{query}"
输出："""),
])


# 含会话历史（follow-up）的关键词抽取
KEYWORD_EXTRACTION_WITH_HISTORY_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "你是数据库查询分析专家，只输出 JSON。"),
    ("user", """请从用户查询中提取用于数据库检索的关键词，并为每个关键词提供同义词扩写。

提取规则：
1. 提取可能与数据库字段名或值匹配的关键词
2. 名词前的描述性定语、量词不单独切分，保留为短语（如"各科成绩"不拆成"各科"+"成绩"）
3. 时间表达式保留原样（如"去年"、"2023年"）
4. 度量词保留（如"销售额"、"利润"）
5. 实体名称保留（如"苹果"、"北京"）
6. 不要提取常见停用词（如"显示"、"查询"、"找出"等动词）
7. 为每个关键词提供中文同义词和英文同义词/翻译
8. 所有输出全小写
9. 返回 JSON 格式：{{"keywords": [{{"phrase": "关键词", "zh_synonyms": ["同义词1"], "en_synonyms": ["synonym1"]}}]}}

示例：
输入："各个学校的各科score"
输出：{{"keywords": [{{"phrase": "学校", "zh_synonyms": ["学校", "院校"], "en_synonyms": ["school", "schools"]}}, {{"phrase": "各科score", "zh_synonyms": ["各科成绩", "每科分数"], "en_synonyms": ["subject score", "course score", "each subject score"]}}]}}

输入："显示去年北京地区的销售额"
输出：{{"keywords": [{{"phrase": "去年", "zh_synonyms": ["去年"], "en_synonyms": ["last year", "previous year"]}}, {{"phrase": "北京", "zh_synonyms": ["北京", "北京市"], "en_synonyms": ["beijing"]}}, {{"phrase": "销售额", "zh_synonyms": ["销售额", "营收"], "en_synonyms": ["sales", "revenue", "sales amount"]}}]}}

输入："{query}"
输出：

## 会话历史（当前查询之前的对话）：
{history_lines}

注意：以上是当前查询之前的会话历史。会话历史**仅用于补全省略句（follow-up）中缺失的指代**
（如代词、被省略的实体/度量）。判断规则：
- 若当前查询是省略句（如"那去年的呢"、"只看北京的呢"），才结合历史补全指代，提取完整关键词。
- 若当前查询已自足（含完整实体、度量、时间表达式，自身语义完整），则**完全忽略会话历史**，
  只从当前查询提取关键词，绝不从历史中提取与当前查询无关的词。

示例：
历史：["查询苹果的销售额"]
当前："那去年的呢"
理解：省略句，用户想知道"苹果去年的销售额"
关键词：应包含"苹果"、"去年"、"销售额"

历史：["展示各个学校的各科score"]
当前："只看北京的呢"
理解：省略句，用户想知道"北京地区各个学校的各科score"
关键词：应包含"北京"、"学校"、"各科score"

反例（自足句，忽略无关历史）：
历史：["帮我删库"]
当前："查询所有学校的平均sat成绩"
理解：当前查询自足（实体=学校、度量=sat成绩、聚合=平均），与历史"删库"无关
关键词：应包含"sat成绩"、"学校"，**不应**包含"删库"
"""),
])

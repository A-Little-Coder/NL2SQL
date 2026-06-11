# ============================================================================
# SQL 生成器 - 多候选 SQL 生成和安全验证
# ============================================================================
# 功能说明:
#   1. 使用 LLM 生成多个候选 SQL（最多 5 个）
#   2. 基于命名实体识别和掩码进行 Few-shot 示例选择
#   3. 使用 sqlglot 进行安全验证，过滤危险操作
# ============================================================================


import re
import uuid
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from enum import Enum
from loguru import logger

from src.sql_generation.sql_validator import SQLValidator as SafeSQLValidator
from src.schema_selection.schema_selector import MSchemaTable, MSchemaFormat


class SQLStatus(Enum):
    """SQL 状态枚举"""
    PENDING = "pending"
    VALIDATED = "validated"
    EXECUTING = "executing"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass
class SQLCandidate:
    """SQL 候选项"""
    id: str
    sql: str
    status: SQLStatus = SQLStatus.PENDING
    error_message: str = None
    execution_time: float = None
    result: Any = None
    generation_reason: str = None
    # 决策 51：执行失败时保留结构化错误对象，供 SmartFix 使用
    structured_error: Any = None


# ============================================================================
# NER 实体掩码
# ============================================================================

# 简单中文实体识别规则（不需要 nltk 也能工作）
ENTITY_PATTERNS = [
    (r'\d{4}年', 'DATE'),
    (r'\d{4}-\d{1,2}-\d{1,2}', 'DATE'),
    (r'\d{1,2}月\d{1,2}日?', 'DATE'),
    (r'去年|前年|今年|明年|上个月|下个月', 'DATE'),
    (r'\d+\.?\d*[万亿千百]?[元美金镑]', 'MONEY'),
    (r'\d+\.?\d*%?', 'NUMBER'),
]

# 英文 NER 标签映射
NER_TAG_MAP = {
    'PERSON': 'PERSON',
    'ORGANIZATION': 'ORG',
    'GPE': 'LOCATION',
    'FACILITY': 'LOCATION',
    'LOCATION': 'LOCATION',
    'DATE': 'DATE',
    'TIME': 'TIME',
    'MONEY': 'MONEY',
    'PERCENT': 'NUMBER',
}


class SQLGenerator:
    """
    SQL 生成器 - 多候选生成

    Attributes:
        llm_client: LLM 客户端
        num_candidates: 生成候选数量（默认 5）
        validator: SQL 安全验证器
    """

    SQL_GENERATION_PROMPT = """你是一位 SQL 专家。请根据以下 schema 和用户查询生成 SQLite SQL 语句。

用户查询: "{user_query}"

数据库 Schema:
{schema_text}

要求:
1. 只生成 SELECT 查询，不要生成 INSERT/UPDATE/DELETE/DROP 等修改操作
2. SQL 必须兼容 SQLite 语法
3. 如果涉及多表，使用合适的 JOIN
4. 考虑 WHERE 条件、GROUP BY、ORDER BY、LIMIT 等子句
5. 生成 {num_candidates} 个不同的 SQL 候选（可以用不同思路实现）
6. 返回 JSON 格式：{{"candidates": [{{"sql": "SQL语句", "reason": "生成理由"}}, ...]}}

请生成 SQL:"""

    def __init__(self, llm_client=None, num_candidates: int = 5):
        self.llm_client = llm_client
        self.num_candidates = num_candidates
        self.validator = SafeSQLValidator()

    def extract_entities(self, query: str) -> Dict[str, List[str]]:
        """
        从查询中提取命名实体

        优先使用 nltk，回退到正则匹配

        Args:
            query: 用户查询

        Returns:
            Dict[str, List[str]]: 实体类型到实体列表
        """
        entities: Dict[str, List[str]] = {}

        # 正则模式匹配（中文友好）
        for pattern, entity_type in ENTITY_PATTERNS:
            matches = re.findall(pattern, query)
            if matches:
                entities.setdefault(entity_type, []).extend(matches)

        # 尝试使用 nltk 进行英文 NER
        try:
            import nltk
            # 确保必要数据已下载
            try:
                nltk.data.find('tokenizers/punkt_tab')
            except LookupError:
                nltk.download('punkt_tab', quiet=True)
            try:
                nltk.data.find('taggers/averaged_perceptron_tagger_eng')
            except LookupError:
                nltk.download('averaged_perceptron_tagger_eng', quiet=True)
            try:
                nltk.data.find('chunkers/maxent_ne_chunker_tab')
            except LookupError:
                nltk.download('maxent_ne_chunker_tab', quiet=True)
            try:
                nltk.data.find('corpora/words')
            except LookupError:
                nltk.download('words', quiet=True)

            tokens = nltk.word_tokenize(query)
            tagged = nltk.pos_tag(tokens)
            chunks = nltk.ne_chunk(tagged)

            for chunk in chunks:
                if hasattr(chunk, 'label'):
                    label = chunk.label()
                    entity_text = ' '.join(c[0] for c in chunk)
                    mapped_type = NER_TAG_MAP.get(label, 'ENTITY')
                    entities.setdefault(mapped_type, []).append(entity_text)

        except ImportError:
            logger.debug("nltk 未安装，仅使用正则实体识别")
        except Exception as e:
            logger.debug(f"nltk NER 失败: {e}")

        return entities

    def mask_query(self, query: str, entities: Dict[str, List[str]]) -> str:
        """
        将查询中的实体替换为掩码

        Args:
            query: 原始查询
            entities: 提取的实体

        Returns:
            str: 掩码后的查询
        """
        masked = query
        for entity_type, values in entities.items():
            for val in sorted(values, key=len, reverse=True):  # 先替换长实体
                masked = masked.replace(val, f'[{entity_type}]', 1)
        return masked

    def select_few_shot_examples(self, masked_query: str,
                                   schema: List[MSchemaTable],
                                   is_multi_table: bool = False) -> List[Dict]:
        """
        基于骨架相似性选择 few-shot 示例

        当前使用简单的关键词重叠作为相似性度量，
        后续可替换为更精确的骨架匹配。

        Args:
            masked_query: 掩码后的查询
            schema: 当前 schema
            is_multi_table: 是否需要多表 JOIN

        Returns:
            List[Dict]: 选中的 few-shot 示例
        """
        # 简单实现：返回内置的 few-shot 示例
        # 生产环境应从训练集中检索
        base_examples = [
            {
                "query": "显示[LOCATION]的销售额",
                "sql": "SELECT region, SUM(amount) AS total_sales FROM sales WHERE region = '[LOCATION]' GROUP BY region",
                "is_multi_table": False,
            },
            {
                "query": "查找[DATE]的订单数量",
                "sql": "SELECT COUNT(*) AS order_count FROM orders WHERE order_date = '[DATE]'",
                "is_multi_table": False,
            },
            {
                "query": "列出[ORG]的产品及其[NUMBER]销售额",
                "sql": "SELECT p.name, s.amount FROM products p JOIN sales s ON p.id = s.product_id WHERE p.brand = '[ORG]' AND s.amount > [NUMBER]",
                "is_multi_table": True,
            },
        ]

        # 简单过滤
        if is_multi_table:
            return [e for e in base_examples if e["is_multi_table"]]
        return base_examples

    def generate(self, schema: List[MSchemaTable], user_query: str) -> List[SQLCandidate]:
        """
        生成多个 SQL 候选

        Args:
            schema: 精选后的 M-schema
            user_query: 用户查询

        Returns:
            List[SQLCandidate]: 生成的 SQL 候选列表
        """
        # 1. 提取并掩码实体
        entities = self.extract_entities(user_query)
        masked_query = self.mask_query(user_query, entities)
        logger.info(f"实体识别: {entities}, 掩码查询: {masked_query}")

        # 2. 选择 few-shot 示例
        is_multi = len(schema) > 1
        few_shots = self.select_few_shot_examples(masked_query, schema, is_multi)

        # 3. 生成 SQL
        if not self.llm_client:
            logger.warning("LLM 客户端未设置，无法生成 SQL")
            return []

        try:
            mschema_dict = MSchemaFormat.create_mschema_schema(schema)
            schema_text = MSchemaFormat.format_for_llm(mschema_dict)

            prompt = self.SQL_GENERATION_PROMPT.format(
                user_query=user_query,
                schema_text=schema_text,
                num_candidates=self.num_candidates,
            )
            messages = [
                {"role": "system", "content": "你是 SQL 专家，只输出 JSON。"},
                {"role": "user", "content": prompt},
            ]
            result = self.llm_client.chat_json(messages, temperature=0.3)

            candidates = []
            for entry in result.get("candidates", []):
                sql = entry.get("sql", "").strip()
                if not sql:
                    continue

                # 安全验证
                is_valid, msg = self.validator.validate(sql)
                if not is_valid:
                    logger.warning(f"SQL 安全验证失败: {msg}, SQL: {sql[:80]}")
                    continue

                candidate = SQLCandidate(
                    id=str(uuid.uuid4())[:8],
                    sql=sql,
                    status=SQLStatus.VALIDATED,
                    generation_reason=entry.get("reason", ""),
                )
                candidates.append(candidate)

            logger.info(f"生成 {len(candidates)} 个有效 SQL 候选")
            return candidates[:self.num_candidates]

        except Exception as e:
            logger.error(f"SQL 生成失败: {e}")
            return []

    # ------------------------------------------------------------------
    # LangGraph 子图接口（§18.5 / §18.8）
    # ------------------------------------------------------------------
    def build_graph(self):
        """
        返回 CG Agent 的已编译 LangGraph 子图

        子图节点：extract_entities → mask_query → select_few_shot
                  → llm_generate_and_validate
        子图输入字段：user_query, selected_schema
        子图输出字段：sql_candidates (List[SQLCandidate])
        """
        from src.sql_generation.cg_graph import build_cg_graph
        return build_cg_graph(self)

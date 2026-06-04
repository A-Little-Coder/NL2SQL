# ============================================================================
# InformationRetrieval 测试用例
# ============================================================================
# 运行方法:
#   python -m unittest tests.retrieval.test_information_retriever -v
# ============================================================================


import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.retrieval.information_retrieval import (
    InformationRetrieval, RetrievedItem, RetrievedContext, KeywordGroup,
    ngram_vote_score, _char_ngrams,
)


class TestSimpleKeywordExtract(unittest.TestCase):
    """测试简单关键词提取回退方案"""

    def test_removes_stopwords(self):
        result = InformationRetrieval._simple_keyword_extract("显示查询")
        for w in result:
            self.assertNotIn(w, {"显示", "查询"})

    def test_empty_input(self):
        result = InformationRetrieval._simple_keyword_extract("")
        self.assertEqual(result, [])

    def test_english_keywords(self):
        result = InformationRetrieval._simple_keyword_extract("find Apple revenue")
        self.assertIn("Apple", result)
        self.assertIn("revenue", result)

    def test_chinese_query_returns_keywords(self):
        result = InformationRetrieval._simple_keyword_extract("苹果公司的营收")
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)


class TestLLMKeywordExtract(unittest.TestCase):
    """测试 LLM 关键词提取（Mock）— 四向同义词扩写 + KeywordGroup 返回"""

    def test_llm_extract_new_format(self):
        """新格式：含 zh_synonyms 和 en_synonyms，返回 KeywordGroup 列表"""
        mock_client = MagicMock()
        mock_client.chat_json.return_value = {
            "keywords": [
                {"phrase": "学校", "zh_synonyms": ["学校", "院校"], "en_synonyms": ["school", "schools"]},
                {"phrase": "各科score", "zh_synonyms": ["各科成绩", "每科分数"], "en_synonyms": ["subject score", "course score"]},
            ]
        }
        ir = InformationRetrieval(llm_client=mock_client)
        result = ir.extract_keywords("各个学校的各科score")
        # 返回 KeywordGroup 列表
        self.assertEqual(len(result), 2)
        self.assertIsInstance(result[0], KeywordGroup)
        self.assertEqual(result[0].phrase, "学校")
        self.assertIn("学校", result[0].terms)
        self.assertIn("院校", result[0].terms)
        self.assertIn("school", result[0].terms)
        self.assertEqual(result[1].phrase, "各科score")
        self.assertIn("各科score", result[1].terms)
        self.assertIn("subject score", result[1].terms)
        # 全小写
        for g in result:
            for t in g.terms:
                self.assertEqual(t, t.lower())
            # 组内无重复
            self.assertEqual(len(g.terms), len(set(g.terms)))

    def test_llm_extract_old_format_compat(self):
        """兼容旧格式：纯字符串列表"""
        mock_client = MagicMock()
        mock_client.chat_json.return_value = {
            "keywords": ["去年", "北京", "销售额"]
        }
        ir = InformationRetrieval(llm_client=mock_client)
        result = ir.extract_keywords("显示去年北京地区的销售额")
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0].phrase, "去年")
        self.assertEqual(result[0].terms, ["去年"])

    def test_llm_extract_fallback(self):
        mock_client = MagicMock()
        mock_client.chat_json.side_effect = Exception("API Error")
        ir = InformationRetrieval(llm_client=mock_client)
        result = ir.extract_keywords("苹果公司的营收")
        # 回退到简单提取，返回 KeywordGroup 列表
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)
        for g in result:
            self.assertIsInstance(g, KeywordGroup)


class TestRetrieveValues(unittest.TestCase):
    """测试 LSH 值检索（Mock）"""

    def test_no_lsh_indexer(self):
        ir = InformationRetrieval()
        result = ir.retrieve_values(["苹果"])
        self.assertEqual(result, [])

    def test_lsh_retrieve_with_mock(self):
        ir = InformationRetrieval(lsh_threshold=0.3)

        mock_lsh_indexer = MagicMock()
        mock_lsh_indexer._loaded_lsh = MagicMock()
        mock_lsh_indexer._loaded_minhashes = MagicMock()
        mock_lsh_indexer.query.return_value = {
            "products": {"name": ["苹果", "苹果汁"]}
        }
        ir.lsh_indexer = mock_lsh_indexer

        # Patch 模块级 LSHIndexer
        with patch('src.retrieval.information_retrieval.LSHIndexer') as MockLSH:
            mock_mh = MagicMock()
            MockLSH.create_minhash.return_value = mock_mh
            MockLSH.jaccard_similarity.return_value = 0.7

            result = ir.retrieve_values(["苹果"], top_k=5)

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].item_type, "value")
        self.assertEqual(result[0].score, 0.7)

    def test_lsh_below_threshold_filtered(self):
        ir = InformationRetrieval(lsh_threshold=0.8)

        mock_lsh_indexer = MagicMock()
        mock_lsh_indexer._loaded_lsh = MagicMock()
        mock_lsh_indexer._loaded_minhashes = MagicMock()
        mock_lsh_indexer.query.return_value = {
            "products": {"name": ["苹果"]}
        }
        ir.lsh_indexer = mock_lsh_indexer

        with patch('src.retrieval.information_retrieval.LSHIndexer') as MockLSH:
            mock_mh = MagicMock()
            MockLSH.create_minhash.return_value = mock_mh
            MockLSH.jaccard_similarity.return_value = 0.3  # 低于阈值

            result = ir.retrieve_values(["苹果"])

        # 应该被过滤掉
        self.assertEqual(len(result), 0)


class TestRetrieveSchema(unittest.TestCase):
    """测试语义 schema 检索（Mock）— 按关键词分组独立召回 + 组内 N-gram 投票精排"""

    def test_no_vector_store(self):
        ir = InformationRetrieval()
        result = ir.retrieve_schema([KeywordGroup(phrase="销售额", terms=["销售额", "sales"])])
        self.assertEqual(result, {})

    def test_schema_retrieve_with_mock(self):
        mock_vs = MagicMock()
        mock_vs.query.return_value = [
            {
                "id": "orders_amount",
                "metadata": {"table_name": "orders", "original_column_name": "amount", "column_name": "sales amount", "item_type": "column"},
                "document": "orders | amount | sales amount",
                "distance": 0.2,
            },
        ]

        mock_vectorizer = MagicMock()
        mock_vectorizer.embed_texts.return_value = {"dense": [[0.1] * 1024]}

        ir = InformationRetrieval(vector_store=mock_vs)
        ir._vectorizer = mock_vectorizer

        groups = [KeywordGroup(phrase="销售额", terms=["销售额", "sales", "revenue"])]
        result = ir.retrieve_schema(groups)
        # 返回 key 为 phrase
        self.assertIn("销售额", result)
        self.assertEqual(len(result["销售额"]), 1)
        self.assertEqual(result["销售额"][0].name, "amount")

    def test_schema_dedup_within_group(self):
        """测试组内同一列去重（取最高 final_score）"""
        mock_vs = MagicMock()
        mock_vs.query.return_value = [
            {
                "id": "orders_amount_1",
                "metadata": {"table_name": "orders", "original_column_name": "amount", "item_type": "column"},
                "document": "orders | amount | sales", "distance": 0.2,
            },
            {
                "id": "orders_amount_2",
                "metadata": {"table_name": "orders", "original_column_name": "amount", "item_type": "column"},
                "document": "orders | amount | sales", "distance": 0.25,
            },
        ]
        mock_vectorizer = MagicMock()
        mock_vectorizer.embed_texts.return_value = {"dense": [[0.1] * 1024]}

        ir = InformationRetrieval(vector_store=mock_vs)
        ir._vectorizer = mock_vectorizer

        groups = [KeywordGroup(phrase="销售额", terms=["sales", "revenue"])]
        result = ir.retrieve_schema(groups)
        # 同一个列只出现一次
        self.assertEqual(len(result["销售额"]), 1)

    def test_independent_group_retrieval(self):
        """测试不同关键词组互不干扰"""
        mock_vs = MagicMock()
        # 两组各自返回不同的列
        mock_vs.query.side_effect = [
            # "学校" 组的第一个 term
            [{"id": "1", "metadata": {"table_name": "schools", "original_column_name": "name", "item_type": "column"},
              "document": "schools | name | school name", "distance": 0.15}],
            # "学校" 组的第二个 term
            [{"id": "2", "metadata": {"table_name": "schools", "original_column_name": "city", "item_type": "column"},
              "document": "schools | city | city", "distance": 0.2}],
            # "score" 组的第一个 term
            [{"id": "3", "metadata": {"table_name": "satscores", "original_column_name": "avgscrread", "item_type": "column"},
              "document": "satscores | avgscrread | average scores in reading", "distance": 0.3}],
        ]
        mock_vectorizer = MagicMock()
        mock_vectorizer.embed_texts.return_value = {"dense": [[0.1] * 1024]}

        ir = InformationRetrieval(vector_store=mock_vs)
        ir._vectorizer = mock_vectorizer

        groups = [
            KeywordGroup(phrase="学校", terms=["school", "schools"]),
            KeywordGroup(phrase="score", terms=["score", "subject score"]),
        ]
        result = ir.retrieve_schema(groups)

        # 两组各自返回结果
        self.assertIn("学校", result)
        self.assertIn("score", result)
        # "学校"组返回 schools 表的列
        school_cols = [c.name for c in result["学校"]]
        self.assertIn("name", school_cols)
        # "score"组返回 satscores 表的列
        score_cols = [c.name for c in result["score"]]
        self.assertIn("avgscrread", score_cols)


class TestRetrieveFull(unittest.TestCase):
    """测试完整的 retrieve 流程"""

    def test_retrieve_with_mocks(self):
        mock_llm = MagicMock()
        mock_llm.chat_json.return_value = {
            "keywords": [
                {"phrase": "苹果", "zh_synonyms": ["苹果"], "en_synonyms": ["apple"]},
                {"phrase": "销售额", "zh_synonyms": ["销售额", "营收"], "en_synonyms": ["sales", "revenue"]},
            ]
        }

        mock_lsh = MagicMock()
        mock_lsh._loaded_lsh = MagicMock()
        mock_lsh._loaded_minhashes = MagicMock()
        mock_lsh.query.return_value = {"products": {"name": ["苹果"]}}

        mock_vs = MagicMock()
        mock_vs.query.return_value = [
            {
                "id": "products_name",
                "metadata": {"table_name": "products", "original_column_name": "name", "column_name": "product name", "item_type": "column"},
                "document": "products | name | product name",
                "distance": 0.15,
            },
        ]

        mock_vectorizer = MagicMock()
        mock_vectorizer.embed_texts.return_value = {"dense": [[0.1] * 1024]}

        ir = InformationRetrieval(
            llm_client=mock_llm,
            lsh_indexer=mock_lsh,
            vector_store=mock_vs,
            lsh_threshold=0.3,
        )
        ir._vectorizer = mock_vectorizer

        with patch('src.retrieval.information_retrieval.LSHIndexer') as MockLSH:
            mock_mh = MagicMock()
            MockLSH.create_minhash.return_value = mock_mh
            MockLSH.jaccard_similarity.return_value = 0.7

            context = ir.retrieve("查一下苹果的销售额")

        self.assertIsInstance(context, RetrievedContext)
        # keyword_groups 应为 KeywordGroup 列表
        self.assertIsInstance(context.keyword_groups, list)
        self.assertEqual(len(context.keyword_groups), 2)
        self.assertEqual(context.keyword_groups[0].phrase, "苹果")
        self.assertEqual(context.keyword_groups[1].phrase, "销售额")
        # keyword_columns_map 应包含每个关键词的召回列
        self.assertIn("苹果", context.keyword_columns_map)
        self.assertIn("销售额", context.keyword_columns_map)
        # 扁平化关键词应包含同义词
        self.assertIn("苹果", context.keywords)
        self.assertIn("apple", context.keywords)
        self.assertIn("sales", context.keywords)


class TestRetrievedContext(unittest.TestCase):
    """测试 RetrievedContext 辅助方法"""

    def test_get_all_table_names(self):
        ctx = RetrievedContext(
            tables=[RetrievedItem(item_type="table", name="orders")],
            columns=[RetrievedItem(item_type="column", name="id", table_name="orders"),
                     RetrievedItem(item_type="column", name="name", table_name="users")],
            values=[RetrievedItem(item_type="value", name="苹果", table_name="products")],
        )
        tables = ctx.get_all_table_names()
        self.assertIn("orders", tables)
        self.assertIn("users", tables)
        self.assertIn("products", tables)

    def test_empty_context(self):
        ctx = RetrievedContext()
        self.assertEqual(ctx.tables, [])
        self.assertEqual(ctx.lsh_hit_count, 0)
        self.assertEqual(ctx.vector_top_scores, [])


class TestEnhanceWithSchema(unittest.TestCase):
    """测试根据值补充 schema 信息"""

    def test_enhance_adds_missing_table_and_column(self):
        ir = InformationRetrieval()
        ctx = RetrievedContext(
            tables=[],
            columns=[],
            values=[
                RetrievedItem(
                    item_type="value", name="苹果",
                    table_name="products", score=0.8,
                    metadata={"column_name": "name"},
                ),
            ],
        )
        result = ir.enhance_with_schema(ctx)
        self.assertTrue(any(t.name == "products" for t in result.tables))
        self.assertTrue(any(c.name == "name" and c.table_name == "products" for c in result.columns))

    def test_enhance_no_duplicate(self):
        """已有表/列不应该重复添加"""
        ir = InformationRetrieval()
        ctx = RetrievedContext(
            tables=[RetrievedItem(item_type="table", name="products")],
            columns=[RetrievedItem(item_type="column", name="name", table_name="products")],
            values=[
                RetrievedItem(
                    item_type="value", name="苹果",
                    table_name="products", score=0.8,
                    metadata={"column_name": "name"},
                ),
            ],
        )
        result = ir.enhance_with_schema(ctx)
        self.assertEqual(len([t for t in result.tables if t.name == "products"]), 1)
        self.assertEqual(len([c for c in result.columns if c.name == "name" and c.table_name == "products"]), 1)


class TestCharNgrams(unittest.TestCase):
    """测试字符级 n-gram 生成"""

    def test_basic_3gram(self):
        result = _char_ngrams("score", 3)
        self.assertIn("sco", result)
        self.assertIn("cor", result)
        self.assertIn("ore", result)
        self.assertEqual(len(result), 3)

    def test_short_text(self):
        result = _char_ngrams("ab", 3)
        self.assertEqual(result, {"ab"})

    def test_empty_text(self):
        result = _char_ngrams("", 3)
        self.assertEqual(result, set())


class TestNgramVoteScore(unittest.TestCase):
    """测试 N-gram 投票得分（只拆解关键词，统计 n-gram 在 document 中的出现次数）"""

    def test_score_matches_with_counts(self):
        """'school' 出现2次应比 'score' 出现1次得分更高"""
        doc = "satscores | school | school name"
        terms = ["school", "score"]
        vote = ngram_vote_score(doc, terms, n=3)
        # "school" → "sch"×2 + "cho"×2 + "hoo"×2 + "ool"×2 = 8
        # "score"  → "sco"×1 + "cor"×1 + "ore"×1 = 3
        # total = 8 + 3 = 11
        self.assertEqual(vote, 11.0)

    def test_repeated_match_counts_multiple(self):
        """同一个 n-gram 出现多次应累计计数"""
        doc = "school school school"
        terms = ["school"]
        vote = ngram_vote_score(doc, terms, n=3)
        # "school" → sch×3, cho×3, hoo×3, ool×3 = 12
        self.assertEqual(vote, 12.0)

    def test_no_match(self):
        doc = "schools | school | school"
        terms = ["score"]
        vote = ngram_vote_score(doc, terms, n=3)
        self.assertEqual(vote, 0.0)

    def test_case_insensitive(self):
        """全小写 document 和 query_terms 应能匹配"""
        doc = "satscores | avgscrread | average scores in reading"
        terms = ["score", "reading"]
        vote = ngram_vote_score(doc, terms, n=3)
        self.assertGreater(vote, 0.0)

    def test_chinese_ngram(self):
        """中文关键词也能做 n-gram 匹配"""
        doc = "schools | school | school"
        terms = ["学校"]
        vote = ngram_vote_score(doc, terms, n=3)
        self.assertEqual(vote, 0.0)


class TestDocumentLowercase(unittest.TestCase):
    """测试 document 全小写"""

    def test_format_column_document_lowercase(self):
        from src.preprocessing.schema_doc_generator import SchemaColumnDocGenerator
        doc = SchemaColumnDocGenerator.format_column_document(
            table_name="SATScores",
            original_column_name="AvgScrRead",
            column_description="Average Scores in Reading",
        )
        self.assertEqual(doc, "satscores | avgscrread | average scores in reading")

    def test_format_column_document_desc_fallback(self):
        from src.preprocessing.schema_doc_generator import SchemaColumnDocGenerator
        # column_description 为空时回退到 value_description
        doc = SchemaColumnDocGenerator.format_column_document(
            table_name="scores",
            original_column_name="Num",
            column_description="",
            value_description="number of test takers",
        )
        self.assertEqual(doc, "scores | num | number of test takers")


if __name__ == "__main__":
    unittest.main()

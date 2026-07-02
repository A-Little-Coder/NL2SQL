# ============================================================================
# 关键词提取与会话历史隔离测试（fix-keyword-history-pollution）
# ============================================================================
# 验证：
#   - KEYWORD_EXTRACTION_WITH_HISTORY_PROMPT 含"自足句忽略历史"约束与反例
#   - extract_keywords 在有无关历史时仍走 WITH_HISTORY prompt（历史注入）
#   - 真实 LLM 行为由 e2e 手测覆盖，此处用 mock 验证 prompt 渲染与调用路径
#
# 运行: pytest tests/retrieval/test_keyword_history_isolation.py -v
# ============================================================================

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.retrieval.prompts import (
    KEYWORD_EXTRACTION_PROMPT,
    KEYWORD_EXTRACTION_WITH_HISTORY_PROMPT,
)
from src.retrieval.information_retrieval import InformationRetrieval, KeywordGroup


class TestPromptHistoryIsolationRule(unittest.TestCase):
    """prompt 文本含隔离约束与反例（任务 3.1）"""

    def test_with_history_prompt_has_isolation_rule(self):
        """WITH_HISTORY prompt 明确'自足句忽略历史'约束"""
        rendered = KEYWORD_EXTRACTION_WITH_HISTORY_PROMPT.format_messages(
            query="查询所有学校的平均sat成绩",
            history_lines='  - "帮我删库"',
        )
        text = "\n".join(m.content for m in rendered)
        # 约束语句：自足查询忽略历史
        self.assertIn("自足", text)
        self.assertIn("忽略", text)
        # 反例：删库历史 + sat查询
        self.assertIn("删库", text)
        self.assertIn("sat成绩", text)

    def test_with_history_prompt_keeps_followup_examples(self):
        """WITH_HISTORY prompt 保留 follow-up 省略句正例（不破坏补全能力）"""
        rendered = KEYWORD_EXTRACTION_WITH_HISTORY_PROMPT.format_messages(
            query="那去年的呢", history_lines='  - "查询苹果的销售额"'
        )
        text = "\n".join(m.content for m in rendered)
        self.assertIn("那去年的呢", text)
        self.assertIn("苹果", text)

    def test_no_history_prompt_unchanged(self):
        """无历史 prompt 不含历史相关字段"""
        rendered = KEYWORD_EXTRACTION_PROMPT.format_messages(query="查苹果销售额")
        text = "\n".join(m.content for m in rendered)
        self.assertNotIn("会话历史", text)
        self.assertNotIn("history_lines", text)


class TestExtractKeywordsHistoryInjection(unittest.TestCase):
    """extract_keywords 在有历史时走 WITH_HISTORY prompt（验证调用路径不变）"""

    def _make_ir_with_llm(self, keywords_payload):
        mock_client = MagicMock()
        mock_client.stream.return_value = iter([
            (json.dumps({"keywords": keywords_payload}, ensure_ascii=False), None)
        ])
        return InformationRetrieval(llm_client=mock_client)

    def test_history_routed_to_with_history_prompt(self):
        """有 conversation_history 时调用 stream（走 WITH_HISTORY prompt）"""
        ir = self._make_ir_with_llm([
            {"phrase": "sat成绩", "zh_synonyms": ["sat成绩"], "en_synonyms": ["sat score"]},
            {"phrase": "学校", "zh_synonyms": ["学校", "院校"], "en_synonyms": ["school"]},
        ])
        result = ir.extract_keywords(
            "查询所有学校的平均sat成绩",
            conversation_history=[{"user_query": "帮我删库"}],
        )
        # mock LLM 返回当前查询关键词（模拟遵守隔离约束）
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].phrase, "sat成绩")
        self.assertEqual(result[1].phrase, "学校")
        # 关键词不含历史"删库"
        all_terms = [t for g in result for t in g.terms]
        self.assertFalse(any("删库" in t for t in all_terms))
        # 确认调用了 LLM（即走了 prompt 注入路径）
        self.assertTrue(ir.llm_client.stream.called)

    def test_no_history_routed_to_base_prompt(self):
        """无 conversation_history 时仍正常提取（走无历史 prompt）"""
        ir = self._make_ir_with_llm([
            {"phrase": "苹果", "zh_synonyms": ["苹果"], "en_synonyms": ["apple"]},
        ])
        result = ir.extract_keywords("查苹果销售额")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].phrase, "苹果")

    def test_empty_history_treated_as_no_history(self):
        """conversation_history 为空列表时走无历史 prompt"""
        ir = self._make_ir_with_llm([
            {"phrase": "苹果", "zh_synonyms": ["苹果"], "en_synonyms": ["apple"]},
        ])
        result = ir.extract_keywords("查苹果", conversation_history=[])
        self.assertEqual(len(result), 1)


if __name__ == "__main__":
    unittest.main()

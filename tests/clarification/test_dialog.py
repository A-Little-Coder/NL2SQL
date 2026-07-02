# ============================================================================
# DialogManager 单元测试（决策 12/13）
# ============================================================================
# 运行: pytest tests/clarification/test_dialog.py -v
# ============================================================================

import unittest
from unittest.mock import patch

from src.clarification.dialog import (
    DialogManager,
    DECLINED,
    MAX_REACHED,
)
from src.clarification.config import load_clarification_config, is_enabled, get_max_rounds


class TestDeclineDetection(unittest.TestCase):
    """拒答关键词识别（决策 13）"""

    def test_default_decline_keywords(self):
        dm = DialogManager()
        for kw in ["不知道", "跳过", "算了", "skip", "不清楚", "随便"]:
            self.assertTrue(dm.is_decline(kw), f"应识别拒答: {kw}")

    def test_decline_in_sentence(self):
        """回答中包含拒答词即识别"""
        dm = DialogManager()
        self.assertTrue(dm.is_decline("我不知道啊"))
        self.assertTrue(dm.is_decline("算了算了"))
        self.assertTrue(dm.is_decline("skip please"))

    def test_case_insensitive(self):
        dm = DialogManager()
        self.assertTrue(dm.is_decline("SKIP"))
        self.assertTrue(dm.is_decline("Skip"))

    def test_normal_answer_not_decline(self):
        dm = DialogManager()
        self.assertFalse(dm.is_decline("水果"))
        self.assertFalse(dm.is_decline("Apple 公司"))
        self.assertFalse(dm.is_decline("去年一年的数据"))

    def test_empty_answer_not_decline(self):
        dm = DialogManager()
        self.assertFalse(dm.is_decline(""))

    def test_custom_keywords(self):
        """自定义拒答关键词"""
        dm = DialogManager(decline_keywords=["拒绝", "no"])
        self.assertTrue(dm.is_decline("我拒绝回答"))
        self.assertTrue(dm.is_decline("no way"))
        # 默认关键词不再生效
        self.assertFalse(dm.is_decline("不知道"))


class TestMaxRounds(unittest.TestCase):
    """5 次硬上限（决策 13）"""

    def test_reached_max(self):
        dm = DialogManager(max_rounds=5)
        self.assertFalse(dm.reached_max(4))
        self.assertTrue(dm.reached_max(5))
        self.assertTrue(dm.reached_max(6))

    def test_custom_max_rounds(self):
        dm = DialogManager(max_rounds=3)
        self.assertFalse(dm.reached_max(2))
        self.assertTrue(dm.reached_max(3))


class TestAskInterrupt(unittest.TestCase):
    """ask() 的 interrupt 包装流程"""

    @patch("src.clarification.dialog.interrupt")
    def test_ask_returns_user_answer_on_resume(self, mock_interrupt):
        """resume 时 interrupt 返回用户回答，ask 透传"""
        mock_interrupt.return_value = "水果"
        dm = DialogManager(max_rounds=5)
        answer = dm.ask("苹果指什么？", clarify_round=0)
        self.assertEqual(answer, "水果")
        # 验证 interrupt 被调用，value 含问题与轮次
        mock_interrupt.assert_called_once()
        call_arg = mock_interrupt.call_args[0][0]
        self.assertEqual(call_arg["question"], "苹果指什么？")
        self.assertEqual(call_arg["round"], 1)

    @patch("src.clarification.dialog.interrupt")
    def test_ask_returns_declined_on_decline_answer(self, mock_interrupt):
        """用户拒答时返回 DECLINED 信号"""
        mock_interrupt.return_value = "不知道"
        dm = DialogManager(max_rounds=5)
        answer = dm.ask("苹果指什么？", clarify_round=0)
        self.assertEqual(answer, DECLINED)
        self.assertTrue(DialogManager.is_declined_signal(answer))

    @patch("src.clarification.dialog.interrupt")
    def test_ask_returns_max_reached_at_limit(self, mock_interrupt):
        """达上限时不调 interrupt，返回 MAX_REACHED"""
        dm = DialogManager(max_rounds=5)
        answer = dm.ask("苹果指什么？", clarify_round=5)
        self.assertEqual(answer, MAX_REACHED)
        # 不应调用 interrupt
        mock_interrupt.assert_not_called()

    @patch("src.clarification.dialog.interrupt")
    def test_ask_passes_ambiguities(self, mock_interrupt):
        """ambiguities 随反问上下文抛给前端"""
        mock_interrupt.return_value = "公司"
        dm = DialogManager()
        dm.ask("苹果指什么？", clarify_round=0,
               ambiguities=[{"entity": "苹果", "candidates": ["公司", "水果"]}])
        call_arg = mock_interrupt.call_args[0][0]
        self.assertEqual(len(call_arg["ambiguities"]), 1)
        self.assertEqual(call_arg["ambiguities"][0]["entity"], "苹果")

    @patch("src.clarification.dialog.interrupt")
    def test_ask_coerces_non_string_answer(self, mock_interrupt):
        """非字符串回答被转为字符串"""
        mock_interrupt.return_value = 12345
        dm = DialogManager()
        answer = dm.ask("问题？", clarify_round=0)
        self.assertEqual(answer, "12345")


class TestDeclineSignal(unittest.TestCase):
    """拒答信号判断"""

    def test_is_declined_signal(self):
        self.assertTrue(DialogManager.is_declined_signal(DECLINED))
        self.assertTrue(DialogManager.is_declined_signal(MAX_REACHED))

    def test_normal_answer_not_signal(self):
        self.assertFalse(DialogManager.is_declined_signal("水果"))
        self.assertFalse(DialogManager.is_declined_signal(""))


class TestConfigLoading(unittest.TestCase):
    """config/clarification.yaml 加载"""

    def test_load_config_has_required_keys(self):
        cfg = load_clarification_config(force_reload=True)
        self.assertIn("enabled", cfg)
        self.assertIn("max_clarify_rounds", cfg)
        self.assertIn("decline_keywords", cfg)

    def test_is_enabled_default_true(self):
        # 配置文件里 enabled: true
        self.assertTrue(is_enabled())

    def test_get_max_rounds(self):
        self.assertEqual(get_max_rounds(), 5)

    def test_config_keywords_match_default(self):
        cfg = load_clarification_config(force_reload=True)
        kws = cfg["decline_keywords"]
        self.assertIn("不知道", kws)
        self.assertIn("skip", kws)


if __name__ == "__main__":
    unittest.main()

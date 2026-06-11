# ============================================================================
# LLMClient 中文思考指令注入测试（决策 51）
# ============================================================================

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class TestInjectChineseThinking(unittest.TestCase):
    """_inject_chinese_thinking 的纯函数行为测试"""

    def _reload_module(self):
        """重新导入 utils.llm_client 以让模块级常量重新从环境变量读取"""
        import importlib

        import utils.llm_client as mod

        importlib.reload(mod)
        return mod

    def test_append_to_existing_system(self):
        """首条为 system message 时应追加到内容末尾"""
        with mock.patch.dict(os.environ, {"LLM_CHINESE_THINKING": "true"}):
            mod = self._reload_module()
            messages = [
                {"role": "system", "content": "你是 SQL 专家"},
                {"role": "user", "content": "查询用户表"},
            ]
            result = mod._inject_chinese_thinking(messages)

            self.assertEqual(len(result), 2)
            self.assertEqual(result[0]["role"], "system")
            self.assertIn("你是 SQL 专家", result[0]["content"])
            self.assertIn("请全程使用中文进行内部思考和推理", result[0]["content"])
            # 原 messages 不被修改
            self.assertEqual(messages[0]["content"], "你是 SQL 专家")

    def test_insert_when_no_system(self):
        """首条不是 system message 时应在开头插入新条"""
        with mock.patch.dict(os.environ, {"LLM_CHINESE_THINKING": "true"}):
            mod = self._reload_module()
            messages = [
                {"role": "user", "content": "查询用户表"},
            ]
            result = mod._inject_chinese_thinking(messages)

            self.assertEqual(len(result), 2)
            self.assertEqual(result[0]["role"], "system")
            self.assertEqual(result[0]["content"], "请全程使用中文进行内部思考和推理。")
            self.assertEqual(result[1]["role"], "user")

    def test_disabled_by_env(self):
        """LLM_CHINESE_THINKING=false 时不注入"""
        with mock.patch.dict(os.environ, {"LLM_CHINESE_THINKING": "false"}):
            mod = self._reload_module()
            messages = [
                {"role": "system", "content": "你是 SQL 专家"},
                {"role": "user", "content": "查询用户表"},
            ]
            result = mod._inject_chinese_thinking(messages)

            # 原样返回
            self.assertEqual(result, messages)
            self.assertEqual(result[0]["content"], "你是 SQL 专家")

    def test_empty_messages(self):
        """空 messages 列表应仅返回中文思考 system 消息"""
        with mock.patch.dict(os.environ, {"LLM_CHINESE_THINKING": "true"}):
            mod = self._reload_module()
            result = mod._inject_chinese_thinking([])

            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["role"], "system")
            self.assertIn("请全程使用中文进行内部思考和推理", result[0]["content"])


class TestLLMClientInjection(unittest.TestCase):
    """LLMClient.chat / chat_stream 入口注入测试（用 mock 拦截 openai 调用）"""

    def setUp(self):
        os.environ.setdefault("QWEN_API_KEY", "test-key")
        os.environ["LLM_CHINESE_THINKING"] = "true"

    def _get_client(self):
        """构造一个不会真实调用 API 的 LLMClient"""
        import importlib

        import utils.llm_client as mod

        importlib.reload(mod)
        return mod, mod.LLMClient(model="test-model")

    def test_blocking_path_injects(self):
        """阻塞路径 _chat_blocking 应注入中文思考"""
        mod, client = self._get_client()

        # 拦截 openai 客户端，捕获实际 messages
        captured = {}

        class FakeResp:
            class _Choice:
                class _Msg:
                    content = '{"ok": true}'

                message = _Msg()

            choices = [_Choice()]

        def fake_create(**kwargs):
            captured["messages"] = kwargs.get("messages")
            return FakeResp()

        client.client.chat.completions.create = fake_create
        client.chat([
            {"role": "system", "content": "你是 SQL 专家"},
            {"role": "user", "content": "查询"},
        ])

        # 注入后的 messages
        msgs = captured["messages"]
        self.assertIn("请全程使用中文进行内部思考和推理", msgs[0]["content"])

    def test_stream_path_injects(self):
        """流式路径 chat_stream 应注入中文思考"""
        mod, client = self._get_client()

        captured = {}

        def fake_stream(**kwargs):
            captured["messages"] = kwargs.get("messages")
            # 返回空 iterator 即可（chat_stream 不会拿到任何 chunk）
            return iter([])

        client.client.chat.completions.create = fake_stream
        client.chat_stream([
            {"role": "user", "content": "查询"},
        ])

        msgs = captured["messages"]
        # 首条应被插入为 system role
        self.assertEqual(msgs[0]["role"], "system")
        self.assertIn("请全程使用中文进行内部思考和推理", msgs[0]["content"])
        # 原 user message 应在第二位
        self.assertEqual(msgs[1]["role"], "user")


if __name__ == "__main__":
    unittest.main()

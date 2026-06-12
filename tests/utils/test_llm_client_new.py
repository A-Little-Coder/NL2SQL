# ============================================================================
# LLMClient 新接口单元测试（OpenSpec migrate-llm-client-to-langchain）
# ============================================================================
# 覆盖：
#   - 构造时 ChatOpenAI 配置（output_version / extra_body）
#   - 入参标准化（List[BaseMessage] / PromptValue / dict 拒绝）
#   - 中文思考注入（用 SystemMessage 对象操作）
#   - invoke / stream / ainvoke / astream 四个公开 API
#   - 模块级辅助函数 accumulate / stream_with_sse / parse_json
#   - chunk content 解析（output_version="responses/v1" 模式的 list[dict]）
# ============================================================================

import asyncio
import importlib
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def _make_chunk(content=None):
    """构造一个伪装的 AIMessageChunk，content 直接传入（list 或 None）"""
    chunk = MagicMock()
    chunk.content = content if content is not None else []
    return chunk


def _block_text(text: str) -> dict:
    """构造 type=text 的 content block"""
    return {"type": "text", "text": text}


def _block_reasoning(text: str) -> dict:
    """构造 type=reasoning 的 content block（含 summary[].text）"""
    return {"type": "reasoning", "summary": [{"text": text, "type": "summary_text"}]}


def _build_client():
    """构造一个不会真发请求的 LLMClient（依赖被替换为 mock）"""
    from utils.llm_client import LLMClient
    with patch.dict(os.environ, {"QWEN_API_KEY": "fake_key"}):
        return LLMClient(model="fake-model")


# ──────────────────────────────────────────────────────────────────────
# 入参标准化与中文思考注入
# ──────────────────────────────────────────────────────────────────────

class TestMessageNormalization(unittest.TestCase):
    """_normalize_messages + _inject_chinese_thinking 的纯函数行为"""

    def _reload_module(self):
        import utils.llm_client as mod
        importlib.reload(mod)
        return mod

    def test_baseMessage_list_passthrough(self):
        """List[BaseMessage] 应原样返回"""
        from langchain_core.messages import HumanMessage, SystemMessage
        client = _build_client()
        msgs = [SystemMessage("你是助手"), HumanMessage("你好")]
        result = client._normalize_messages(msgs)
        self.assertEqual(len(result), 2)
        self.assertIs(result[0], msgs[0])
        self.assertIs(result[1], msgs[1])

    def test_prompt_value_to_messages(self):
        """PromptValue 应调 to_messages() 转换"""
        from langchain_core.prompts import ChatPromptTemplate
        client = _build_client()
        tmpl = ChatPromptTemplate.from_messages([
            ("system", "sys"),
            ("user", "{q}"),
        ])
        pv = tmpl.format_prompt(q="hello")
        result = client._normalize_messages(pv)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].content, "sys")
        self.assertEqual(result[1].content, "hello")

    def test_dict_messages_rejected(self):
        """dict 入参应抛 TypeError"""
        client = _build_client()
        with self.assertRaises(TypeError) as ctx:
            client._normalize_messages([{"role": "user", "content": "x"}])
        self.assertIn("BaseMessage", str(ctx.exception))

    def test_non_list_rejected(self):
        """非 list / 非 PromptValue 应抛 TypeError"""
        client = _build_client()
        with self.assertRaises(TypeError):
            client._normalize_messages("string is not allowed")


class TestChineseThinkingInjection(unittest.TestCase):
    """_inject_chinese_thinking 的 BaseMessage 对象操作"""

    def _reload_module(self):
        import utils.llm_client as mod
        importlib.reload(mod)
        return mod

    def test_append_when_first_is_system(self):
        """首条为 SystemMessage 时追加到 content 末尾"""
        from langchain_core.messages import HumanMessage, SystemMessage
        with patch.dict(os.environ, {"LLM_CHINESE_THINKING": "true"}):
            mod = self._reload_module()
            with patch.dict(os.environ, {"QWEN_API_KEY": "fake_key"}):
                client = mod.LLMClient(model="fake")
            messages = [SystemMessage("你是 SQL 专家"), HumanMessage("查询")]
            result = client._inject_chinese_thinking(messages)
            self.assertEqual(len(result), 2)
            self.assertIsInstance(result[0], SystemMessage)
            self.assertIn("你是 SQL 专家", result[0].content)
            self.assertIn("请全程使用中文进行内部思考和推理", result[0].content)
            # 原 messages 不被改
            self.assertEqual(messages[0].content, "你是 SQL 专家")

    def test_insert_when_first_is_not_system(self):
        """首条不是 SystemMessage 时在头部插入新条"""
        from langchain_core.messages import HumanMessage, SystemMessage
        with patch.dict(os.environ, {"LLM_CHINESE_THINKING": "true"}):
            mod = self._reload_module()
            with patch.dict(os.environ, {"QWEN_API_KEY": "fake_key"}):
                client = mod.LLMClient(model="fake")
            messages = [HumanMessage("查询")]
            result = client._inject_chinese_thinking(messages)
            self.assertEqual(len(result), 2)
            self.assertIsInstance(result[0], SystemMessage)
            self.assertEqual(result[0].content, "请全程使用中文进行内部思考和推理。")
            self.assertEqual(result[1].content, "查询")

    def test_disabled_by_env(self):
        """LLM_CHINESE_THINKING=false 时不注入"""
        from langchain_core.messages import HumanMessage
        with patch.dict(os.environ, {"LLM_CHINESE_THINKING": "false"}):
            mod = self._reload_module()
            with patch.dict(os.environ, {"QWEN_API_KEY": "fake_key"}):
                client = mod.LLMClient(model="fake")
            messages = [HumanMessage("查询")]
            result = client._inject_chinese_thinking(messages)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].content, "查询")

    def test_empty_messages_get_single_system(self):
        """空消息列表注入后只有一条 SystemMessage"""
        from langchain_core.messages import SystemMessage
        with patch.dict(os.environ, {"LLM_CHINESE_THINKING": "true"}):
            mod = self._reload_module()
            with patch.dict(os.environ, {"QWEN_API_KEY": "fake_key"}):
                client = mod.LLMClient(model="fake")
            result = client._inject_chinese_thinking([])
            self.assertEqual(len(result), 1)
            self.assertIsInstance(result[0], SystemMessage)


# ──────────────────────────────────────────────────────────────────────
# Chunk 解析（responses/v1 list[dict]）
# ──────────────────────────────────────────────────────────────────────

class TestChunkBlockExtraction(unittest.TestCase):

    def test_text_only_chunk(self):
        from utils.llm_client import LLMClient
        chunk = _make_chunk(content=[_block_text("abc")])
        text, reasoning = LLMClient._extract_chunk_blocks(chunk)
        self.assertEqual(text, "abc")
        self.assertIsNone(reasoning)

    def test_reasoning_only_chunk(self):
        from utils.llm_client import LLMClient
        chunk = _make_chunk(content=[_block_reasoning("思考中")])
        text, reasoning = LLMClient._extract_chunk_blocks(chunk)
        self.assertIsNone(text)
        self.assertEqual(reasoning, "思考中")

    def test_mixed_blocks_in_chunk(self):
        """同一 chunk 含 reasoning + text 两种 block"""
        from utils.llm_client import LLMClient
        chunk = _make_chunk(content=[
            _block_reasoning("思"),
            _block_text("结果"),
        ])
        text, reasoning = LLMClient._extract_chunk_blocks(chunk)
        self.assertEqual(text, "结果")
        self.assertEqual(reasoning, "思")

    def test_empty_content_chunk(self):
        from utils.llm_client import LLMClient
        chunk = _make_chunk(content=[])
        text, reasoning = LLMClient._extract_chunk_blocks(chunk)
        self.assertIsNone(text)
        self.assertIsNone(reasoning)

    def test_str_content_fallback(self):
        """非 list 类型的 content 走兜底分支"""
        from utils.llm_client import LLMClient
        chunk = _make_chunk(content="raw string")
        text, reasoning = LLMClient._extract_chunk_blocks(chunk)
        self.assertEqual(text, "raw string")
        self.assertIsNone(reasoning)

    def test_reasoning_with_multiple_summary_items(self):
        """reasoning block 的 summary 列表多条都累加"""
        from utils.llm_client import LLMClient
        chunk = _make_chunk(content=[{
            "type": "reasoning",
            "summary": [
                {"text": "第一段"},
                {"text": "第二段"},
            ],
        }])
        text, reasoning = LLMClient._extract_chunk_blocks(chunk)
        self.assertIsNone(text)
        self.assertEqual(reasoning, "第一段第二段")


class TestMessageTextExtraction(unittest.TestCase):
    """阻塞 invoke 返回 AIMessage 的 content 解析"""

    def test_str_content(self):
        from langchain_core.messages import AIMessage
        from utils.llm_client import LLMClient
        msg = AIMessage(content="plain text  ")
        self.assertEqual(LLMClient._extract_text_from_message(msg), "plain text")

    def test_list_content_with_text_block(self):
        from langchain_core.messages import AIMessage
        from utils.llm_client import LLMClient
        msg = AIMessage(content=[_block_text("hello"), _block_text(" world")])
        self.assertEqual(LLMClient._extract_text_from_message(msg), "hello world")

    def test_list_content_with_reasoning_ignored(self):
        """阻塞 invoke 中 reasoning block 应被忽略，只返回 text"""
        from langchain_core.messages import AIMessage
        from utils.llm_client import LLMClient
        msg = AIMessage(content=[_block_reasoning("思考"), _block_text("结果")])
        self.assertEqual(LLMClient._extract_text_from_message(msg), "结果")


# ──────────────────────────────────────────────────────────────────────
# 公开 API：invoke / stream（mock _chat_model）
# ──────────────────────────────────────────────────────────────────────

class TestInvokeAndStream(unittest.TestCase):

    def test_invoke_returns_str(self):
        from langchain_core.messages import AIMessage, HumanMessage
        client = _build_client()
        # 用 mock 替换 _bind_runtime 返回值——它本身就是抽象后的 mock 锚点
        fake_runnable = MagicMock()
        fake_runnable.invoke = MagicMock(
            return_value=AIMessage(content=[_block_text("hello")])
        )
        with patch.object(client, "_bind_runtime", return_value=fake_runnable):
            result = client.invoke([HumanMessage("hi")])
        self.assertEqual(result, "hello")

    def test_invoke_as_json_returns_dict(self):
        from langchain_core.messages import AIMessage, HumanMessage
        client = _build_client()
        fake_runnable = MagicMock()
        fake_runnable.invoke = MagicMock(
            return_value=AIMessage(content=[_block_text('{"k": 1}')])
        )
        with patch.object(client, "_bind_runtime",
                          return_value=fake_runnable) as bind_mock:
            result = client.invoke([HumanMessage("hi")], as_json=True)
        self.assertEqual(result, {"k": 1})
        # _bind_runtime 应被调用并 as_json=True
        call_args = bind_mock.call_args
        self.assertTrue(call_args.args[2])  # 第 3 个参数是 as_json

    def test_invoke_bind_runtime_kwargs(self):
        from langchain_core.messages import AIMessage, HumanMessage
        client = _build_client()
        fake_runnable = MagicMock()
        fake_runnable.invoke = MagicMock(
            return_value=AIMessage(content=[_block_text("ok")])
        )
        with patch.object(client, "_bind_runtime",
                          return_value=fake_runnable) as bind_mock:
            client.invoke([HumanMessage("x")], temperature=0.5, max_tokens=100)
        # 入参顺序: (temperature, max_tokens, as_json)
        args = bind_mock.call_args.args
        self.assertEqual(args[0], 0.5)
        self.assertEqual(args[1], 100)
        self.assertFalse(args[2])  # as_json=False

    def test_stream_yields_tuples(self):
        from langchain_core.messages import HumanMessage
        client = _build_client()

        fake_chunks = [
            _make_chunk(content=[_block_reasoning("思")]),
            _make_chunk(content=[_block_text("ab")]),
            _make_chunk(content=[_block_reasoning("考"), _block_text("c")]),
        ]
        fake_runnable = MagicMock()
        fake_runnable.stream = MagicMock(return_value=iter(fake_chunks))
        with patch.object(client, "_bind_runtime", return_value=fake_runnable):
            results = list(client.stream([HumanMessage("x")]))
        self.assertEqual(results[0], (None, "思"))
        self.assertEqual(results[1], ("ab", None))
        self.assertEqual(results[2], ("c", "考"))


class TestAsyncAPI(unittest.TestCase):

    def test_ainvoke_returns_str(self):
        from langchain_core.messages import AIMessage, HumanMessage

        async def _go():
            client = _build_client()
            fake_runnable = MagicMock()

            async def _async_return(_msgs):
                return AIMessage(content=[_block_text("async result")])

            fake_runnable.ainvoke = MagicMock(side_effect=_async_return)
            with patch.object(client, "_bind_runtime", return_value=fake_runnable):
                return await client.ainvoke([HumanMessage("hi")])

        result = asyncio.run(_go())
        self.assertEqual(result, "async result")

    def test_astream_yields_tuples(self):
        from langchain_core.messages import HumanMessage

        async def _async_gen():
            yield _make_chunk(content=[_block_text("a")])
            yield _make_chunk(content=[_block_reasoning("思")])

        async def _go():
            client = _build_client()
            fake_runnable = MagicMock()
            fake_runnable.astream = MagicMock(return_value=_async_gen())
            with patch.object(client, "_bind_runtime", return_value=fake_runnable):
                return [t async for t in client.astream([HumanMessage("x")])]

        results = asyncio.run(_go())
        self.assertEqual(results, [("a", None), (None, "思")])


# ──────────────────────────────────────────────────────────────────────
# 辅助函数 accumulate / stream_with_sse / parse_json
# ──────────────────────────────────────────────────────────────────────

class TestAccumulate(unittest.TestCase):

    def test_accumulate_drops_reasoning(self):
        from utils.llm_client import accumulate
        stream = iter([("a", "思"), ("bc", None), (None, "考"), ("d", None)])
        self.assertEqual(accumulate(stream), "abcd")

    def test_accumulate_empty_stream(self):
        from utils.llm_client import accumulate
        self.assertEqual(accumulate(iter([])), "")


class TestStreamWithSSE(unittest.TestCase):

    def test_no_emitter_silent_accumulate(self):
        """没有 emitter 时仍能累积正文，不抛错"""
        from utils.llm_client import stream_with_sse
        stream = iter([("a", "思"), ("b", None)])
        result = stream_with_sse(stream)
        self.assertEqual(result, "ab")

    def test_reasoning_pushed_to_sse(self):
        """有 emitter 时 reasoning 应推 llm_thinking 事件"""
        from src.api.streaming import current_emitter, current_node
        from utils.llm_client import stream_with_sse

        fake_emitter = MagicMock()
        token_e = current_emitter.set(fake_emitter)
        token_n = current_node.set("test_node")
        try:
            stream = iter([("a", "思"), ("b", "考"), ("c", None)])
            result = stream_with_sse(stream)
            self.assertEqual(result, "abc")
            # 两次 reasoning 推送
            self.assertEqual(fake_emitter.emit.call_count, 2)
            calls = fake_emitter.emit.call_args_list
            self.assertEqual(calls[0].args, ("llm_thinking", {"node": "test_node", "text": "思"}))
            self.assertEqual(calls[1].args, ("llm_thinking", {"node": "test_node", "text": "考"}))
        finally:
            current_emitter.reset(token_e)
            current_node.reset(token_n)

    def test_emit_failure_does_not_break_accumulation(self):
        """SSE 推送异常不影响主流程累积"""
        from src.api.streaming import current_emitter
        from utils.llm_client import stream_with_sse

        bad_emitter = MagicMock()
        bad_emitter.emit = MagicMock(side_effect=RuntimeError("boom"))
        token = current_emitter.set(bad_emitter)
        try:
            stream = iter([("a", "思"), ("b", None)])
            result = stream_with_sse(stream)
            self.assertEqual(result, "ab")
        finally:
            current_emitter.reset(token)


class TestParseJson(unittest.TestCase):

    def test_valid_json(self):
        from utils.llm_client import parse_json
        self.assertEqual(parse_json('{"k": 1}'), {"k": 1})

    def test_json_with_surrounding_text(self):
        from utils.llm_client import parse_json
        self.assertEqual(parse_json('前缀 {"a": 2} 后缀'), {"a": 2})

    def test_unparseable_returns_raw(self):
        from utils.llm_client import parse_json
        result = parse_json("纯文本无 JSON")
        self.assertEqual(result, {"raw_response": "纯文本无 JSON"})

    def test_empty_string_returns_raw(self):
        from utils.llm_client import parse_json
        self.assertEqual(parse_json(""), {"raw_response": ""})


# ──────────────────────────────────────────────────────────────────────
# 构造时 ChatOpenAI 配置
# ──────────────────────────────────────────────────────────────────────

class TestChatOpenAIConfiguration(unittest.TestCase):

    def test_output_version_set(self):
        """ChatOpenAI 实例应该被配置为 output_version=responses/v1"""
        client = _build_client()
        # ChatOpenAI 的 output_version 字段
        self.assertEqual(getattr(client._chat_model, "output_version", None), "responses/v1")

    def test_extra_body_contains_enable_thinking(self):
        """extra_body 应含 enable_thinking 字段"""
        client = _build_client()
        extra = getattr(client._chat_model, "extra_body", None)
        self.assertIsInstance(extra, dict)
        self.assertIn("enable_thinking", extra)

    def test_no_self_client_alias(self):
        """LLMClient 不应再暴露 self.client / self.chat_model 公开属性"""
        client = _build_client()
        self.assertFalse(hasattr(client, "client"))
        self.assertFalse(hasattr(client, "chat_model"))
        # 私有属性存在
        self.assertTrue(hasattr(client, "_chat_model"))


if __name__ == "__main__":
    unittest.main()

"""StreamEmitter + LLMClient 流式集成测试（适配 invoke/stream 新接口）"""

import asyncio
import contextvars
import threading
from unittest.mock import MagicMock, patch

import pytest

from src.api.streaming import (
    StreamEmitter,
    current_emitter,
    current_node,
    emit_safe,
)
from utils.llm_client import accumulate, parse_json, stream_with_sse


# ── StreamEmitter ─────────────────────────────────────────────

def test_emitter_cross_thread_delivery():
    """从同步线程 emit 的事件应能被 asyncio loop 收到"""
    loop = asyncio.new_event_loop()
    try:
        queue: asyncio.Queue = asyncio.Queue()
        emitter = StreamEmitter(queue, loop)

        async def producer_and_collect():
            def producer():
                for i in range(5):
                    emitter.emit("test", {"i": i})

            t = threading.Thread(target=producer)
            t.start()
            t.join()

            received = []
            for _ in range(5):
                evt = await asyncio.wait_for(queue.get(), timeout=2.0)
                received.append(evt)
            return received

        received = loop.run_until_complete(producer_and_collect())
        assert len(received) == 5
        assert [e["data"]["i"] for e in received] == [0, 1, 2, 3, 4]
    finally:
        loop.close()


def test_emitter_swallows_after_loop_closed():
    """loop 关闭后 emit 不应抛出"""
    loop = asyncio.new_event_loop()
    queue: asyncio.Queue = asyncio.Queue()
    emitter = StreamEmitter(queue, loop)
    loop.close()
    # 不应抛
    emitter.emit("test", {"x": 1})


# ── contextvars 传递 ─────────────────────────────────────────

def test_current_emitter_default_is_none():
    assert current_emitter.get() is None
    assert current_node.get() is None


def test_emit_safe_silent_when_no_emitter():
    """无 emitter 时调用 emit_safe 静默不发"""
    emit_safe("anything", {"k": "v"})


def test_emit_safe_uses_current_emitter():
    fake_emitter = MagicMock()
    token = current_emitter.set(fake_emitter)
    try:
        emit_safe("test", {"x": 1})
        fake_emitter.emit.assert_called_once_with("test", {"x": 1})
    finally:
        current_emitter.reset(token)


def test_contextvar_crosses_thread_via_copy_context():
    """contextvars.copy_context().run() 应能跨线程传递 current_emitter"""
    loop = asyncio.new_event_loop()
    try:
        queue: asyncio.Queue = asyncio.Queue()
        emitter = StreamEmitter(queue, loop)

        token = current_emitter.set(emitter)
        try:
            async def runner():
                def worker():
                    assert current_emitter.get() is emitter
                    emit_safe("from_thread", {"ok": True})

                ctx = contextvars.copy_context()
                await loop.run_in_executor(None, lambda: ctx.run(worker))
                return await asyncio.wait_for(queue.get(), timeout=2.0)

            evt = loop.run_until_complete(runner())
            assert evt["type"] == "from_thread"
            assert evt["data"]["ok"] is True
        finally:
            current_emitter.reset(token)
    finally:
        loop.close()


# ── LLMClient 辅助函数 ───────────────────────────────────────

def _build_llm_client():
    """构造一个不会真实连接 API 的 LLMClient"""
    from utils.llm_client import LLMClient
    with patch.dict("os.environ", {"QWEN_API_KEY": "fake_key"}):
        return LLMClient(model="fake-model")


def _make_block_text(text: str) -> dict:
    return {"type": "text", "text": text}


def _make_block_reasoning(text: str) -> dict:
    return {"type": "reasoning", "summary": [{"text": text, "type": "summary_text"}]}


def _make_chunk(content=None):
    """构造 mock AIMessageChunk（用 MagicMock 模拟）"""
    chunk = MagicMock()
    chunk.content = content if content is not None else []
    return chunk


# ── accumulate ────────────────────────────────────────────────

def test_accumulate_normal():
    """accumulate 应正确累积多个 content chunk"""
    stream = iter([("a", None), ("bc", None), (None, "思"), ("d", None)])
    assert accumulate(stream) == "abcd"


def test_accumulate_empty():
    assert accumulate(iter([])) == ""


# ── parse_json ────────────────────────────────────────────────

def test_parse_json_valid():
    assert parse_json('{"k": 1}') == {"k": 1}


def test_parse_json_with_surrounding_text():
    assert parse_json('前缀 {"a": 2} 后缀') == {"a": 2}


def test_parse_json_unparseable():
    result = parse_json("纯文本")
    assert result == {"raw_response": "纯文本"}


# ── stream_with_sse ──────────────────────────────────────────

def test_stream_with_sse_no_emitter():
    """无 emitter 时静默累积"""
    stream = iter([("a", None), ("b", "思")])
    assert stream_with_sse(stream) == "ab"


def test_stream_with_sse_auto_pushes_thinking():
    """有 emitter 时 reasoning chunk 应自动推 SSE"""
    fake_emitter = MagicMock()
    token_e = current_emitter.set(fake_emitter)
    token_n = current_node.set("mynode")
    try:
        stream = iter([("a", "思"), ("b", None), ("c", "考")])
        result = stream_with_sse(stream)
        assert result == "abc"
        assert fake_emitter.emit.call_count == 2
        calls = fake_emitter.emit.call_args_list
        assert calls[0].args == ("llm_thinking", {"node": "mynode", "text": "思"})
        assert calls[1].args == ("llm_thinking", {"node": "mynode", "text": "考"})
    finally:
        current_emitter.reset(token_e)
        current_node.reset(token_n)


def test_stream_with_sse_emit_failure_swallow():
    """SSE 推送异常不应影响累积"""
    bad_emitter = MagicMock()
    bad_emitter.emit.side_effect = RuntimeError("boom")
    token = current_emitter.set(bad_emitter)
    try:
        stream = iter([("a", "x"), ("b", None)])
        assert stream_with_sse(stream) == "ab"
    finally:
        current_emitter.reset(token)


# ── LLMClient.invoke 功能验证 ──────────────────────────────

def test_invoke_returns_str():
    from utils.llm_client import LLMClient
    from langchain_core.messages import AIMessage, HumanMessage

    with patch.dict("os.environ", {"QWEN_API_KEY": "fake_key"}):
        client = LLMClient(model="fake-model")

    fake_runnable = MagicMock()
    fake_runnable.invoke = MagicMock(
        return_value=AIMessage(content=[_make_block_text("hello!")])
    )
    with patch.object(client, "_bind_runtime", return_value=fake_runnable):
        result = client.invoke([HumanMessage("hi")])
    assert result == "hello!"


def test_invoke_as_json_returns_dict():
    from utils.llm_client import LLMClient
    from langchain_core.messages import AIMessage, HumanMessage

    with patch.dict("os.environ", {"QWEN_API_KEY": "fake_key"}):
        client = LLMClient(model="fake-model")

    fake_runnable = MagicMock()
    fake_runnable.invoke = MagicMock(
        return_value=AIMessage(content=[_make_block_text('{"k": 1}')])
    )
    with patch.object(client, "_bind_runtime", return_value=fake_runnable):
        result = client.invoke([HumanMessage("hi")], as_json=True)
    assert result == {"k": 1}


def test_stream_yields_content_reasoning():
    from utils.llm_client import LLMClient
    from langchain_core.messages import HumanMessage

    with patch.dict("os.environ", {"QWEN_API_KEY": "fake_key"}):
        client = LLMClient(model="fake-model")

    fake_chunks = [
        _make_chunk(content=[_make_block_reasoning("思")]),
        _make_chunk(content=[_make_block_text("a")]),
        _make_chunk(content=[_make_block_reasoning("考"), _make_block_text("b")]),
    ]
    fake_runnable = MagicMock()
    fake_runnable.stream = MagicMock(return_value=iter(fake_chunks))
    with patch.object(client, "_bind_runtime", return_value=fake_runnable):
        results = list(client.stream([HumanMessage("x")]))
    assert results == [(None, "思"), ("a", None), ("b", "考")]


# ── reasoning 提取（output_version=responses/v1 block 解析）──

def test_reasoning_content_extracted_from_blocks():
    """responses/v1 模式下 reasoning block 的 summary 应被提取"""
    from utils.llm_client import LLMClient
    chunk = _make_chunk(content=[_make_block_reasoning("我"), _make_block_text("结果")])
    text, reasoning = LLMClient._extract_chunk_blocks(chunk)
    assert text == "结果"
    assert reasoning == "我"


def test_chunk_without_reasoning_fields():
    from utils.llm_client import LLMClient
    chunk = _make_chunk(content=[_make_block_text("only text")])
    text, reasoning = LLMClient._extract_chunk_blocks(chunk)
    assert text == "only text"
    assert reasoning is None


def test_chunk_str_content_fallback():
    """非 responses/v1 模式的 content 兜底"""
    from utils.llm_client import LLMClient
    chunk = _make_chunk(content="raw string")
    text, reasoning = LLMClient._extract_chunk_blocks(chunk)
    assert text == "raw string"
    assert reasoning is None


# ── 中文思考注入（BaseMessage 版） ────────────────────────

def test_inject_chinese_thinking_appends_to_system():
    from langchain_core.messages import SystemMessage, HumanMessage
    from utils.llm_client import LLMClient

    with patch.dict("os.environ", {"QWEN_API_KEY": "fake_key", "LLM_CHINESE_THINKING": "true"}):
        import importlib
        import utils.llm_client as mod
        importlib.reload(mod)
        client = mod.LLMClient(model="fake-model")

    msgs = [SystemMessage("你是专家"), HumanMessage("hi")]
    result = client._inject_chinese_thinking(msgs)
    assert len(result) == 2
    assert "你是专家" in result[0].content
    assert "请全程使用中文进行内部思考和推理" in result[0].content
    # 原列表不修改
    assert msgs[0].content == "你是专家"


def test_inject_chinese_thinking_inserts_for_user_first():
    from langchain_core.messages import HumanMessage
    from utils.llm_client import LLMClient

    with patch.dict("os.environ", {"QWEN_API_KEY": "fake_key", "LLM_CHINESE_THINKING": "true"}):
        import importlib
        import utils.llm_client as mod
        importlib.reload(mod)
        client = mod.LLMClient(model="fake-model")

    msgs = [HumanMessage("hi")]
    result = client._inject_chinese_thinking(msgs)
    assert len(result) == 2
    assert result[0].content == "请全程使用中文进行内部思考和推理。"


# ── dict 入参拒绝 ──────────────────────────────────────────

def test_dict_messages_rejected():
    from utils.llm_client import LLMClient
    with patch.dict("os.environ", {"QWEN_API_KEY": "fake_key"}):
        client = LLMClient(model="fake-model")

    import pytest
    with pytest.raises(TypeError):
        client.invoke([{"role": "user", "content": "x"}])
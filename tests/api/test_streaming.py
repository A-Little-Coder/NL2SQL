"""StreamEmitter + LLMClient 流式集成测试（决策 50）"""

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


# ── LLMClient.chat_stream ────────────────────────────────────

def _make_chunk(content=None, reasoning=None):
    """构造一个模拟的 OpenAI 流式 chunk"""
    delta = MagicMock()
    delta.content = content
    delta.reasoning_content = reasoning
    choice = MagicMock()
    choice.delta = delta
    chunk = MagicMock()
    chunk.choices = [choice]
    return chunk


def _build_llm_client():
    """构造一个不会真实连接 API 的 LLMClient"""
    from utils.llm_client import LLMClient
    with patch.dict("os.environ", {"QWEN_API_KEY": "fake_key"}):
        return LLMClient(model="fake-model")


def test_chat_stream_calls_on_thinking_for_reasoning_chunks():
    client = _build_llm_client()
    thinking_calls = []

    fake_stream = [
        _make_chunk(reasoning="我"),
        _make_chunk(reasoning="在思考"),
        _make_chunk(content='{"answer":'),
        _make_chunk(content='"yes"}'),
        _make_chunk(reasoning="..."),
    ]
    client.client.chat.completions.create = MagicMock(return_value=iter(fake_stream))

    full = client.chat_stream(
        [{"role": "user", "content": "x"}],
        on_thinking=lambda s: thinking_calls.append(s),
    )

    assert full == '{"answer":"yes"}'
    assert thinking_calls == ["我", "在思考", "..."]


def test_chat_stream_no_callbacks_for_content():
    """正文 content chunk 不应触发任何回调（决策 50：不推 llm_chunk）"""
    client = _build_llm_client()
    thinking_calls = []
    chunks_seen = []

    fake_stream = [
        _make_chunk(content="abc"),
        _make_chunk(content="def"),
    ]
    client.client.chat.completions.create = MagicMock(return_value=iter(fake_stream))

    # chat_stream 没有 on_chunk 参数 —— 这就是决策 50 的关键
    full = client.chat_stream(
        [{"role": "user", "content": "x"}],
        on_thinking=lambda s: thinking_calls.append(s),
    )
    assert full == "abcdef"
    assert thinking_calls == []
    assert chunks_seen == []


def test_chat_stream_handles_missing_reasoning_content():
    """非 qwen3 模型 delta.reasoning_content 为 None，不应推 thinking 事件"""
    client = _build_llm_client()
    thinking_calls = []

    fake_stream = [
        _make_chunk(content="hi"),
        _make_chunk(content=", world"),
    ]
    client.client.chat.completions.create = MagicMock(return_value=iter(fake_stream))

    full = client.chat_stream(
        [{"role": "user", "content": "x"}],
        on_thinking=lambda s: thinking_calls.append(s),
    )
    assert full == "hi, world"
    assert thinking_calls == []


# ── chat_json 向后兼容 ────────────────────────────────────────

def test_chat_json_blocking_when_no_emitter():
    """无 current_emitter 时 chat_json 走旧阻塞路径"""
    client = _build_llm_client()

    fake_response = MagicMock()
    fake_response.choices = [MagicMock(message=MagicMock(content='{"k": 1}'))]
    client.client.chat.completions.create = MagicMock(return_value=fake_response)

    # 注意没设 current_emitter
    result = client.chat_json([{"role": "user", "content": "x"}])
    assert result == {"k": 1}
    # 应走非流式（stream=True 没被传入）
    call_kwargs = client.client.chat.completions.create.call_args.kwargs
    assert call_kwargs.get("stream") is not True


def test_chat_json_streams_when_emitter_set():
    """有 current_emitter 时 chat_json 走 chat_stream"""
    client = _build_llm_client()

    fake_stream = [
        _make_chunk(reasoning="思考中..."),
        _make_chunk(content='{"k":'),
        _make_chunk(content=' 2}'),
    ]
    client.client.chat.completions.create = MagicMock(return_value=iter(fake_stream))

    fake_emitter = MagicMock()
    token = current_emitter.set(fake_emitter)
    node_token = current_node.set("test_node")
    try:
        result = client.chat_json([{"role": "user", "content": "x"}])
    finally:
        current_emitter.reset(token)
        current_node.reset(node_token)

    assert result == {"k": 2}
    call_kwargs = client.client.chat.completions.create.call_args.kwargs
    assert call_kwargs["stream"] is True
    # 应推送 llm_thinking 事件
    fake_emitter.emit.assert_called_once_with(
        "llm_thinking", {"node": "test_node", "text": "思考中..."}
    )


def test_chat_json_parses_with_regex_fallback():
    """正文 JSON 不规范时正则兜底"""
    client = _build_llm_client()

    fake_response = MagicMock()
    fake_response.choices = [MagicMock(message=MagicMock(
        content='前缀垃圾...{"k": 3}后缀垃圾'
    ))]
    client.client.chat.completions.create = MagicMock(return_value=fake_response)

    result = client.chat_json([{"role": "user", "content": "x"}])
    assert result == {"k": 3}

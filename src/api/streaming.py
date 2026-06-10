"""
SSE 真流式基础设施（决策 50）

提供：
- StreamEmitter：线程安全的 SSE 事件发射器（同步线程 → asyncio.Queue）
- current_emitter：contextvar，跨线程传递发射器
- current_node：contextvar，记录当前所在 graph 节点（用于 llm_thinking 自动标注 node）
- emit_safe(event_type, data)：便捷函数，无 emitter 时静默不发

设计要点（决策 50）：
- 不推送 LLM 正文 token（JSON 片段对用户无价值）
- 推送 qwen3 reasoning_content（自然语言思考链）作为 llm_thinking 事件
- 业务 LLM 返回完整后由节点解析为结构化事件（keywords / answerability 等）
- 心跳由 query.py 的 event_stream 主循环负责（每 15s 无事件 yield `: heartbeat`）
"""

import asyncio
from contextvars import ContextVar
from typing import Any, Optional


class StreamEmitter:
    """线程安全的 SSE 事件发射器

    设计：
    - 在 SSE handler 的 asyncio loop 中创建
    - 通过 contextvar 传递给同步线程（graph 执行）
    - emit() 用 loop.call_soon_threadsafe 把事件推入 asyncio.Queue
    """

    def __init__(self, queue: "asyncio.Queue", loop: asyncio.AbstractEventLoop):
        self.queue = queue
        self.loop = loop

    def emit(self, event_type: str, data: Any) -> None:
        """从任意线程往 asyncio.Queue 推一个 SSE 事件"""
        evt = {"type": event_type, "data": data}
        try:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, evt)
        except RuntimeError:
            # loop 已关闭：静默丢弃，避免在 shutdown 时炸出栈
            pass


# 跨线程传递 emitter / 当前节点名
current_emitter: ContextVar[Optional[StreamEmitter]] = ContextVar(
    "current_emitter", default=None
)
current_node: ContextVar[Optional[str]] = ContextVar(
    "current_node", default=None
)


def emit_safe(event_type: str, data: Any) -> None:
    """便捷函数：当前线程有 emitter 时推送事件，否则静默不发

    用于业务代码（节点、LLMClient 等），无需关心是否在 SSE 上下文中。
    """
    emitter = current_emitter.get()
    if emitter is None:
        return
    emitter.emit(event_type, data)

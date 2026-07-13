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
    - 持有 query_id：emit() 时自动把 query_id 合并进 data，让所有 SSE 事件
      payload 都带上请求级 ID（§7b 决策；Q4=b 全量带）
    """

    def __init__(
        self,
        queue: "asyncio.Queue",
        loop: asyncio.AbstractEventLoop,
        query_id: str = "",
    ):
        self.queue = queue
        self.loop = loop
        self.query_id = query_id

    def emit(self, event_type: str, data: Any) -> None:
        """从任意线程往 asyncio.Queue 推一个 SSE 事件

        当 data 为 dict 且不含 query_id 时，自动注入 self.query_id；
        非 dict 数据（罕见，例如纯字符串）按原样发送。
        """
        if isinstance(data, dict) and self.query_id and "query_id" not in data:
            payload = {**data, "query_id": self.query_id}
        else:
            payload = data
        evt = {"type": event_type, "data": payload}
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

# 运行时注入对象（决策 12：checkpointer 会序列化 state，Python 对象实例不能进 state，
# 改用 ContextVar 传递；API 层在 run_graph 里 set，节点里 get）
current_user_memory: ContextVar[Optional[Any]] = ContextVar(
    "current_user_memory", default=None
)
current_session_memory: ContextVar[Optional[Any]] = ContextVar(
    "current_session_memory", default=None
)
# per-db 的 SQLFixLoop（决策 51：SmartFix 用；每 db 独立，不能进 state，否则 checkpointer 序列化报错）
current_fix_loop: ContextVar[Optional[Any]] = ContextVar(
    "current_fix_loop", default=None
)
# 请求级取消信号（change clarify-choice-inspector-cancel）：threading.Event，
# _wrap_node 在每个节点（含子图节点）开始前检查，set 则抛 CancelRequested 终止图
current_cancel_event: ContextVar[Optional[Any]] = ContextVar(
    "current_cancel_event", default=None
)


def get_user_memory_ctx() -> Optional[Any]:
    """从 ContextVar 取当前请求的 UserMemory 实例（无则 None）。"""
    return current_user_memory.get()


def get_session_memory_ctx() -> Optional[Any]:
    """从 ContextVar 取当前请求的 SessionMemory 实例（无则 None）。"""
    return current_session_memory.get()


def get_fix_loop_ctx() -> Optional[Any]:
    """从 ContextVar 取当前 db 的 SQLFixLoop 实例（无则 None）。"""
    return current_fix_loop.get()


def emit_safe(event_type: str, data: Any) -> None:
    """便捷函数：当前线程有 emitter 时推送事件，否则静默不发

    用于业务代码（节点、LLMClient 等），无需关心是否在 SSE 上下文中。
    """
    emitter = current_emitter.get()
    if emitter is None:
        return
    emitter.emit(event_type, data)

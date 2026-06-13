"""query_id 基础设施测试（§8.0 / §7b 决策）

覆盖：
  - StreamEmitter 自动给 dict data 注入 query_id
  - StreamEmitter 已有 query_id 时不覆盖
  - StreamEmitter query_id 为空时不注入
  - create_initial_state 接受 query_id 参数
  - NL2SQLState 中 query_id 字段在节点装饰器日志中出现
  - 两个并发请求 query_id 唯一性（uuid4 hex 取 12 位）
"""

import asyncio
import logging
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from src.api.streaming import StreamEmitter
from src.graph.state import NL2SQLState, create_initial_state


# ────────────────────────────────────────────────────────────
# StreamEmitter 注入 query_id
# ────────────────────────────────────────────────────────────


def _new_loop_emitter(query_id: str = "qid_test_1234"):
    """构造一个不会真正运行的 loop + emitter（仅用于断言行为）"""
    loop = asyncio.new_event_loop()
    queue: asyncio.Queue = asyncio.Queue()
    emitter = StreamEmitter(queue, loop, query_id=query_id)
    return loop, queue, emitter


def test_emitter_injects_query_id_into_dict_data():
    """emit() 时 dict 类型 data 应自动合并 query_id"""
    loop, queue, emitter = _new_loop_emitter("qid_abc")
    try:
        emitter.emit("stage", {"node": "ir", "status": "started"})

        async def grab():
            return await asyncio.wait_for(queue.get(), timeout=2.0)

        evt = loop.run_until_complete(grab())
        assert evt["type"] == "stage"
        assert evt["data"] == {"node": "ir", "status": "started", "query_id": "qid_abc"}
    finally:
        loop.close()


def test_emitter_does_not_override_existing_query_id():
    """如果 data 里已经有 query_id，emit 不应覆盖（罕见但需保护）"""
    loop, queue, emitter = _new_loop_emitter("qid_abc")
    try:
        emitter.emit("stage", {"query_id": "preset", "node": "ir"})

        async def grab():
            return await asyncio.wait_for(queue.get(), timeout=2.0)

        evt = loop.run_until_complete(grab())
        assert evt["data"]["query_id"] == "preset"
    finally:
        loop.close()


def test_emitter_no_qid_when_emitter_qid_empty():
    """emitter.query_id 为空字符串时不应注入字段"""
    loop, queue, emitter = _new_loop_emitter("")
    try:
        emitter.emit("stage", {"node": "ir"})

        async def grab():
            return await asyncio.wait_for(queue.get(), timeout=2.0)

        evt = loop.run_until_complete(grab())
        assert "query_id" not in evt["data"]
    finally:
        loop.close()


def test_emitter_passes_through_non_dict_data():
    """非 dict 数据按原样送出（不强行包装）"""
    loop, queue, emitter = _new_loop_emitter("qid_abc")
    try:
        emitter.emit("stage", "raw string")

        async def grab():
            return await asyncio.wait_for(queue.get(), timeout=2.0)

        evt = loop.run_until_complete(grab())
        assert evt["data"] == "raw string"
    finally:
        loop.close()


def test_emitter_default_qid_empty():
    """构造时不传 query_id 默认为空，向后兼容"""
    loop = asyncio.new_event_loop()
    try:
        queue: asyncio.Queue = asyncio.Queue()
        emitter = StreamEmitter(queue, loop)
        assert emitter.query_id == ""
    finally:
        loop.close()


# ────────────────────────────────────────────────────────────
# NL2SQLState / create_initial_state
# ────────────────────────────────────────────────────────────


def test_create_initial_state_accepts_query_id():
    """create_initial_state 应接受 query_id 参数并写入 state"""
    state = create_initial_state(
        user_query="测试查询",
        user_id="alice",
        query_id="qid_xyz_0001",
    )
    assert state["query_id"] == "qid_xyz_0001"
    assert state["user_query"] == "测试查询"


def test_create_initial_state_default_query_id_empty():
    """未传 query_id 时默认空字符串（CLI/离线场景兼容）"""
    state = create_initial_state(user_query="x")
    assert state["query_id"] == ""


# ────────────────────────────────────────────────────────────
# query_id 唯一性
# ────────────────────────────────────────────────────────────


def test_uuid_query_id_unique_across_calls():
    """两次生成的 query_id 应不同（uuid4 hex 取 12 位）"""
    qids = {uuid4().hex[:12] for _ in range(1000)}
    # 1000 次中冲突概率极低
    assert len(qids) == 1000


def test_uuid_query_id_length():
    """query_id 应为 12 位 hex"""
    qid = uuid4().hex[:12]
    assert len(qid) == 12
    assert all(c in "0123456789abcdef" for c in qid)


# ────────────────────────────────────────────────────────────
# main_graph._wrap_node 装饰器日志注入 [qid=...]
# ────────────────────────────────────────────────────────────


def test_wrap_node_logs_qid_in_started_and_done(caplog):
    """节点 enter/exit 应输出 [qid=...] 日志"""
    from src.graph.main_graph import _wrap_node

    # loguru → standard logging 桥接（caplog 抓不到 loguru 默认 handler）
    import sys
    from loguru import logger as lg

    handler_id = lg.add(
        sys.stderr,
        level="INFO",
        format="{message}",
    )

    # 改用 PropagateHandler 把 loguru 推到 std logging
    import logging as std_logging

    class PropagateHandler(std_logging.Handler):
        def emit(self, record):
            std_logging.getLogger(record.name).handle(record)

    propagate_handler = PropagateHandler()
    propagate_id = lg.add(propagate_handler, format="{message}")

    try:
        caplog.set_level(std_logging.INFO)

        def fn(state):
            return {"x": 1}

        wrapped = _wrap_node("test_node", fn)
        wrapped({"query_id": "qid_LOG_01"})

        # 校验入口与出口日志都包含 qid 与 node 名
        log_text = "\n".join(r.getMessage() for r in caplog.records)
        assert "qid=qid_LOG_01" in log_text
        assert "node=test_node" in log_text
        assert "status=started" in log_text
        assert "status=done" in log_text
    finally:
        lg.remove(handler_id)
        lg.remove(propagate_id)


def test_wrap_node_logs_qid_on_exception(caplog):
    """节点抛异常时也应有 [qid=...] 错误日志"""
    from src.graph.main_graph import _wrap_node

    import sys
    import logging as std_logging
    from loguru import logger as lg

    class PropagateHandler(std_logging.Handler):
        def emit(self, record):
            std_logging.getLogger(record.name).handle(record)

    propagate_handler = PropagateHandler()
    propagate_id = lg.add(propagate_handler, format="{message}")

    try:
        caplog.set_level(std_logging.INFO)

        def fn(state):
            raise ValueError("boom!")

        wrapped = _wrap_node("bad_node", fn)
        with pytest.raises(ValueError):
            wrapped({"query_id": "qid_ERR_99"})

        log_text = "\n".join(r.getMessage() for r in caplog.records)
        assert "qid=qid_ERR_99" in log_text
        assert "bad_node" in log_text
    finally:
        lg.remove(propagate_id)


def test_wrap_node_handles_missing_query_id(caplog):
    """state 没有 query_id 字段时，日志应输出 [qid=]（空），不报错"""
    from src.graph.main_graph import _wrap_node

    import logging as std_logging
    from loguru import logger as lg

    class PropagateHandler(std_logging.Handler):
        def emit(self, record):
            std_logging.getLogger(record.name).handle(record)

    propagate_id = lg.add(PropagateHandler(), format="{message}")

    try:
        caplog.set_level(std_logging.INFO)

        wrapped = _wrap_node("noqid_node", lambda s: {})
        wrapped({})  # 无 query_id

        log_text = "\n".join(r.getMessage() for r in caplog.records)
        assert "qid=" in log_text
        assert "noqid_node" in log_text
    finally:
        lg.remove(propagate_id)

"""D1（change: enhance-ir-display-and-layout）: _wrap_node 放行 GraphInterrupt

验证：
- 节点抛 GraphInterrupt（interrupt() 反问控制流）时，_wrap_node 直接 re-raise，
  不 emit error 事件 -- 修复反问场景双发 error+clarification
- 普通异常仍正常 emit error + re-raise（对照回归）
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def _get_graph_interrupt():
    """获取 langgraph 的 GraphInterrupt 异常类（兼容不同版本位置）"""
    for mod_name in ("langgraph.errors", "langgraph.types"):
        try:
            mod = __import__(mod_name, fromlist=["GraphInterrupt"])
            cls = getattr(mod, "GraphInterrupt", None)
            if cls is not None:
                return cls
        except ImportError:
            continue
    pytest.skip("当前 langgraph 版本无 GraphInterrupt 可导入")


def test_wrap_node_passthrough_graph_interrupt(monkeypatch):
    """GraphInterrupt 应被放行 re-raise，且不 emit error 事件"""
    from src.graph import main_graph

    GraphInterrupt = _get_graph_interrupt()

    emitted = []
    monkeypatch.setattr(main_graph, "emit_safe", lambda t, d: emitted.append((t, d)))
    # 跳过 current_node ContextVar 设置（测试环境无需）
    monkeypatch.setattr(main_graph, "current_node", None)

    def raising_fn(state):
        raise GraphInterrupt({"question": "test clarify"})

    wrapped = main_graph._wrap_node("test_node", raising_fn)

    with pytest.raises(GraphInterrupt):
        wrapped({"query_id": "q1"})

    # 关键断言：不 emit 任何 error 事件
    error_events = [e for e in emitted if e[0] == "error"]
    assert error_events == [], (
        f"GraphInterrupt 是控制流信号不应 emit error，实际 emit: {error_events}"
    )
    # stage started 仍应发出（done 因异常不会发）
    stage_started = [
        e for e in emitted
        if e[0] == "stage" and e[1].get("status") == "started"
    ]
    assert len(stage_started) == 1


def test_wrap_node_emits_error_on_real_exception(monkeypatch):
    """对照：普通异常仍 emit error + re-raise（回归保护）"""
    from src.graph import main_graph

    emitted = []
    monkeypatch.setattr(main_graph, "emit_safe", lambda t, d: emitted.append((t, d)))
    monkeypatch.setattr(main_graph, "current_node", None)

    def raising_fn(state):
        raise ValueError("boom")

    wrapped = main_graph._wrap_node("test_node", raising_fn)

    with pytest.raises(ValueError):
        wrapped({"query_id": "q1"})

    error_events = [e for e in emitted if e[0] == "error"]
    assert len(error_events) == 1
    assert "boom" in error_events[0][1]["error"]
    assert error_events[0][1]["node"] == "test_node"


def test_is_graph_interrupt_class_name_fallback():
    """_is_graph_interrupt 类名回退：对同名的自定义异常返回 True"""
    from src.graph.main_graph import _is_graph_interrupt

    # 构造一个类名为 GraphInterrupt 的异常（模拟 langgraph 不可用或位置变更）
    FakeGI = type("GraphInterrupt", (Exception,), {})
    assert _is_graph_interrupt(FakeGI("x")) is True

    # 普通异常不匹配
    assert _is_graph_interrupt(ValueError("x")) is False

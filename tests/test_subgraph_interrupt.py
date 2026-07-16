"""方案 B 可行性验证（task 4.4）

验证：single_query_graph 作为 subgraph 编译进 main_graph 后，子图内的 interrupt
能被主图 checkpointer 接管，且可通过 Command(resume=...) 恢复。
- 通过 → 方案 B 成立，权限节点可在子图内 interrupt 反问；
- 失败 → 回退方案 C（两阶段重跑 ir/ss）。
"""

from typing import TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt


class S(TypedDict, total=False):
    value: str
    choice: str
    after_interrupt: str


def _build_subgraph():
    """最小子图：a(interrupt) -> b"""
    g = StateGraph(S)

    def node_a(state: S):
        choice = state.get("choice")
        if not choice:
            choice = interrupt({"question": "继续脱敏?", "kind": "permission_choice"})
        return {"choice": choice}

    def node_b(state: S):
        return {"after_interrupt": f"got:{state.get('choice')}"}

    g.add_node("a", node_a)
    g.add_node("b", node_b)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", END)
    return g.compile()


def _build_main(subgraph):
    """主图：把子图作为 subgraph 节点编译进来，挂 checkpointer"""
    g = StateGraph(S)
    g.add_node("run_sub", subgraph)
    g.add_edge(START, "run_sub")
    g.add_edge("run_sub", END)
    return g.compile(checkpointer=InMemorySaver())


def test_subgraph_interrupt_then_resume():
    """子图内 interrupt 被主图 checkpointer 接管，resume 后恢复执行"""
    main = _build_main(_build_subgraph())
    config = {"configurable": {"thread_id": "t1"}}

    # 第一次 stream：子图 node_a 触发 interrupt
    list(main.stream({"value": "x"}, config=config))
    snap = main.get_state(config)
    assert snap.next, "应处于 interrupt 挂起状态（next 非空）"

    # resume=mask：从 interrupt 恢复，node_a 拿到 choice，node_b 执行
    list(main.stream(Command(resume="mask"), config=config))
    snap2 = main.get_state(config)
    assert snap2.values.get("choice") == "mask"
    assert snap2.values.get("after_interrupt") == "got:mask"
    assert not snap2.next, "resume 后应执行完毕（next 为空）"


def test_subgraph_no_interrupt_when_choice_prefilled():
    """choice 预置时不 interrupt（模拟 flag 关闭/直通/第二次进入）"""
    main = _build_main(_build_subgraph())
    config = {"configurable": {"thread_id": "t2"}}
    list(main.stream({"value": "x", "choice": "mask"}, config=config))
    snap = main.get_state(config)
    assert snap.values.get("after_interrupt") == "got:mask"
    assert not snap.next

"""请求终止测试（change clarify-choice-inspector-cancel）

验证后端合作式取消逻辑的接线（cancel_event / 断连检测分支存在且不破坏正常流）。

注：Starlette TestClient 会缓冲整个流式响应，无法真实模拟"客户端中途断开"--
服务端在客户端 break 前已跑完整条图，故"graph 在节点边界提前退出"这一行为
无法在此单测中复现。该行为由以下方式验证：
  - learn/cancel-stream/06_full_cancel/：可独立运行的最小复刻，真实展示取消
  - Playwright E2E（tests/e2e/request-cancel.spec.ts）：真浏览器 abort fetch

本文件保留一个接线测试：确认取消信号接线后，正常请求仍能跑完并释放 db_ctx。
"""
import json
import threading
import time
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


class _NormalGraph:
    """正常跑完的图：yield 一个 final_sql 让 result/done 能发。"""

    def stream(self, initial_state, **kwargs):
        time.sleep(0.05)
        yield {"history_cache": {"cache_hit": False}}
        yield {"decision": {"final_sql": "SELECT 1", "final_result": [{"x": 1}]}}


@pytest.fixture
def patched_app(tmp_path, monkeypatch):
    from src.api import deps
    from src.memory.session_manager import SessionManager

    sm = SessionManager(base_dir=str(tmp_path / "sessions"), max_cache_size=20)

    class _UM:
        def get_metric_definitions(self, min_confidence=0.7):
            return []

        def get_query_preferences(self):
            return {}

    monkeypatch.setattr(deps, "_session_manager", sm)
    monkeypatch.setattr(deps, "_event_cache", MagicMock())
    monkeypatch.setattr(
        deps, "_globals", MagicMock(data_dir=str(tmp_path), memory_dir=str(tmp_path / "memory"))
    )
    monkeypatch.setattr(deps, "_user_memory_cache", {})
    monkeypatch.setattr(deps, "get_user_memory", lambda user_id: _UM())

    def _factory(graph_obj):
        class _Pool:
            def __init__(self):
                self.released = 0

            def acquire(self, db_id):
                ctx = MagicMock()
                ctx.graph = graph_obj
                ctx.fix_loop = None
                return ctx

            def release(self, db_id):
                self.released += 1

            def stats(self):
                return {"max": 2, "size": 0, "cached": []}

        pool = _Pool()
        monkeypatch.setattr(deps, "_db_pool", pool)
        return pool

    from src.api.app import app
    return app, _factory


def test_cancel_wiring_does_not_break_normal_flow(patched_app):
    """取消信号接线（cancel_event / except CancelledError / finally set）后，
    正常请求仍能跑完：收到 result + done，且 db_ctx 被 release。"""
    app, factory = patched_app
    pool = factory(_NormalGraph())

    client = TestClient(app, raise_server_exceptions=False)
    seen_types = []
    with client.stream(
        "POST",
        "/api/v1/query",
        json={"query": "q", "session_id": "s_normal", "user_id": "u", "db_id": "any"},
    ) as resp:
        for raw in resp.iter_lines():
            line = raw if isinstance(raw, str) else raw.decode("utf-8", "replace")
            if line.startswith("data:"):
                try:
                    seen_types.append(json.loads(line[5:].strip())["type"])
                except Exception:
                    pass

    assert "result" in seen_types
    assert seen_types[-1] == "done"
    # finally 中 pool.release 必须执行（取消接线不影响正常释放）
    assert pool.released >= 1


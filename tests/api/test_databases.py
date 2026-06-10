"""数据库列表与表清单接口测试（决策 49）"""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def patched_app(tmp_path, monkeypatch):
    """构造一个不依赖真实 BGE / 文件系统的最小 app"""
    from src.api import deps

    # 准备假 data_dir：放两个空目录模拟 db
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    for db_id in ("ca_schools", "fin"):
        sub = data_dir / db_id
        sub.mkdir()
        # 触发 _find_databases：必须有 <db_id>.sqlite 或 *.sqlite
        (sub / f"{db_id}.sqlite").write_bytes(b"")

    # mock globals
    fake_globals = MagicMock(data_dir=str(data_dir))

    # mock pool：acquire 返回带 mock connector 的 ctx
    class _MockPool:
        def acquire(self, db_id):
            ctx = MagicMock()
            ctx.connector.get_tables.return_value = [f"{db_id}_t1", f"{db_id}_t2"]
            self._last_acquired = db_id
            return ctx

        def release(self, db_id):
            self._last_released = db_id

        def stats(self):
            return {"max": 2, "size": 0, "cached": []}

    pool = _MockPool()

    monkeypatch.setattr(deps, "_globals", fake_globals)
    monkeypatch.setattr(deps, "_db_pool", pool)
    monkeypatch.setattr(deps, "_session_manager", MagicMock())

    from src.api.app import app
    return app, pool


def test_list_databases(patched_app):
    app, _ = patched_app
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/v1/databases")
    assert resp.status_code == 200
    body = resp.json()
    db_ids = {item["db_id"] for item in body["databases"]}
    assert db_ids == {"ca_schools", "fin"}


def test_get_database_tables(patched_app):
    app, pool = patched_app
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/v1/databases/ca_schools/tables")
    assert resp.status_code == 200
    body = resp.json()
    assert body["db_id"] == "ca_schools"
    assert body["tables"] == ["ca_schools_t1", "ca_schools_t2"]
    assert pool._last_acquired == "ca_schools"
    assert pool._last_released == "ca_schools"


def test_get_tables_nonexistent_db_returns_404(patched_app, monkeypatch):
    """不存在的 db 应被 pool.acquire 抛 FileNotFoundError，路由转为 404"""
    app, pool = patched_app

    def _raise(_):
        raise FileNotFoundError("no such db")

    monkeypatch.setattr(pool, "acquire", _raise)

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/v1/databases/nope/tables")
    assert resp.status_code == 404

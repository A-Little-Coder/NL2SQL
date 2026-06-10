"""数据库列表与表清单接口（决策 49）

- GET /api/v1/databases                 — 列出 data/ 下所有可用数据库
- GET /api/v1/databases/{db_id}/tables  — 列出指定数据库的表清单
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from src.api.deps import get_db_pool, get_globals
from src.api.schemas import DatabaseInfo, DatabaseListResponse, TableListResponse

router = APIRouter()


def _find_databases(data_dir: str):
    """扫描 data/ 目录返回所有可用 db_id 和路径"""
    data_path = Path(data_dir)
    if not data_path.exists():
        return []
    found = []
    for entry in sorted(data_path.iterdir()):
        if not entry.is_dir():
            continue
        # 跳过非数据库目录
        if entry.name in ("preprocessed", "sessions", "user_memory"):
            continue
        db_path = None
        for ext in (".sqlite", ".db"):
            candidate = entry / f"{entry.name}{ext}"
            if candidate.exists():
                db_path = str(candidate)
                break
        if db_path is None:
            for f in entry.glob("*.sqlite"):
                db_path = str(f)
                break
        if db_path is not None:
            found.append((entry.name, db_path))
    return found


@router.get("/databases", response_model=DatabaseListResponse)
async def list_databases(globals_=Depends(get_globals)):
    """列出 data/ 下所有可用数据库"""
    databases = _find_databases(globals_.data_dir)
    return DatabaseListResponse(
        databases=[DatabaseInfo(db_id=db_id, db_path=db_path) for db_id, db_path in databases]
    )


@router.get("/databases/{db_id}/tables", response_model=TableListResponse)
async def get_database_tables(db_id: str, pool=Depends(get_db_pool)):
    """列出指定数据库的表清单（按需加载该 db）"""
    try:
        ctx = pool.acquire(db_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=f"数据库不存在: {db_id} ({e})")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"加载数据库失败: {e}")
    try:
        tables = ctx.connector.get_tables()
        return TableListResponse(db_id=db_id, tables=tables)
    finally:
        pool.release(db_id)

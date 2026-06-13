"""NL2SQL 问数服务 FastAPI 应用

使用方式：
  python run_api.py            # 启动单 worker 服务
  python run_api.py --db_id california_schools  # 启动并预加载指定 db

启动时通过 init_globals() 一次性加载：
  BGE-M3 / VectorStore / LLM / Generator / Decider / AnswerabilityChecker /
  HistoryCache / MemoryUpdater / SessionManager / DbContextPool

每个 db_id 的 DbContext 通过 DbContextPool 按需懒加载（LRU）。
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from src.api.deps import (
    get_db_pool,
    init_globals,
    shutdown_globals,
)
from src.api.routes import databases as databases_router
from src.api.routes import query as query_router
from src.api.routes import session as session_router
from src.api.routes import user as user_router
from utils.langsmith_bootstrap import log_langsmith_status


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期

    startup：调 init_globals() 加载所有全局单例 + DbContextPool。
             如 app.state.warmup_db_id 已设置，则预加载该 db。
    shutdown：释放所有 DbContext。
    """
    # startup
    log_langsmith_status()  # §8.1.1：确认 LangSmith tracing 状态
    data_dir = getattr(app.state, "data_dir", None)
    init_globals(data_dir=data_dir)

    warmup_db_id = getattr(app.state, "warmup_db_id", None)
    if warmup_db_id:
        logger.info(f"warm-up 预加载: {warmup_db_id}")
        try:
            pool = get_db_pool()
            pool.warm_up(warmup_db_id)
        except Exception as e:
            logger.warning(f"warm-up 失败 (不影响启动): {e}")

    yield

    # shutdown
    logger.info("服务关闭，释放所有 DbContext")
    shutdown_globals()


app = FastAPI(
    title="NL2SQL 问数服务",
    description="基于自然语言查询数据库的 API 服务（决策 49：多数据库分池）",
    version="0.2.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 路由注册
app.include_router(query_router.router, prefix="/api/v1")
app.include_router(session_router.router, prefix="/api/v1")
app.include_router(user_router.router, prefix="/api/v1")
app.include_router(databases_router.router, prefix="/api/v1")


@app.get("/api/v1/health")
async def health_check():
    """健康检查（包含 DbContextPool 状态）"""
    try:
        pool = get_db_pool()
        pool_stats = pool.stats()
    except Exception:
        pool_stats = None
    return {
        "status": "ok",
        "service": "nl2sql",
        "db_pool": pool_stats,
    }

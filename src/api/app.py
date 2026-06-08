"""NL2SQL 问数服务 FastAPI 应用

使用方式：
  uvicorn src.api.app:app --reload
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import query as query_router
from src.api.routes import session as session_router
from src.api.routes import user as user_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期

    启动时初始化各组件（组件需手动调用 init_components）
    关闭时清理资源。
    """
    # startup: 组件初始化由调用方负责
    yield
    # shutdown: 清理
    # Phase 2 可在此处关闭连接池等


app = FastAPI(
    title="NL2SQL 问数服务",
    description="基于自然语言查询数据库的 API 服务",
    version="0.1.0",
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


@app.get("/api/v1/health")
async def health_check():
    """健康检查"""
    return {"status": "ok", "service": "nl2sql"}

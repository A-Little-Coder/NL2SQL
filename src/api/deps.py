"""API 依赖注入（决策 49 重构后）

新模型：
    - 全局单例（BGE-M3 / VectorStore / LLM / Generator / Decider /
      AnswerabilityChecker / HistoryCache / MemoryUpdater / SessionManager）
      在 lifespan startup 时由 init_globals() 一次性构造。
    - 每个 db_id 的 DbContext 通过 DbContextPool 按需懒加载（LRU）。
    - 用户记忆按 user_id 用进程 LRU 缓存（沿用原有逻辑）。

FastAPI 依赖项：
    get_session_manager()            → SessionManager
    get_user_memory(user_id)         → UserMemory
    get_db_pool()                    → DbContextPool
    get_globals()                    → Globals

调用方在路由内部使用 pool.acquire(db_id) / pool.release(db_id) 控制生命周期，
不通过 FastAPI 依赖直接拿 DbContext（因为 db_id 需要从 body 取，
依赖函数无法访问 body）。
"""

import os
from pathlib import Path
from typing import Dict, Optional

from loguru import logger

from src.api.db_pool import DbContextPool, Globals
from src.decision.self_consistency import SelfConsistencyDecision
from src.memory.history_cache import HistoryCache
from src.memory.memory_updater import MemoryUpdater
from src.memory.session_manager import SessionManager
from src.memory.user_memory import UserMemory
from src.preprocessing.schema_vectorizer import SchemaVectorizer
from src.preprocessing.vector_store import VectorStoreManager
from src.sql_generation.sql_generator import SQLGenerator
from src.verification.answerability import AnswerabilityChecker
from src.verification.result_verifier import ResultVerifier
from utils.llm_client import LLMClient


# ---------------------------------------------------------------------------
# 模块级全局变量（lifespan startup 中初始化）
# ---------------------------------------------------------------------------

_globals: Optional[Globals] = None
_session_manager: Optional[SessionManager] = None
_db_pool: Optional[DbContextPool] = None

# UserMemory 进程级 LRU
_user_memory_cache: Dict[str, UserMemory] = {}
_USER_MEMORY_CACHE_MAX = 100


# ---------------------------------------------------------------------------
# 启动初始化
# ---------------------------------------------------------------------------

def init_globals(data_dir: Optional[str] = None) -> None:
    """构造所有全局单例 + DbContextPool

    由 src/api/app.py 的 lifespan startup 钩子调用。

    Args:
        data_dir: data/ 根目录绝对路径。None 时取项目根的 data/。
    """
    global _globals, _session_manager, _db_pool

    if data_dir is None:
        # 默认项目根的 data/
        data_dir = str(Path(__file__).parent.parent.parent / "data")

    bge_model_path = os.getenv("BGE_M3_MODEL_PATH", "BAAI/bge-m3")
    model_name = os.getenv("QWEN_MODEL", "qwen3.6-plus")
    pool_max_size = int(os.getenv("DB_POOL_MAX_SIZE", "2"))

    # 1. BGE-M3 向量化器（全局单例，加载慢）
    logger.info(f"加载 BGE-M3 模型: {bge_model_path}")
    vectorizer = SchemaVectorizer(model_name=bge_model_path, device="cpu")
    vectorizer.load_model()

    # 2. VectorStore（共享 chroma collection）
    persist_dir = Path(data_dir) / "preprocessed" / "chroma"
    if not persist_dir.exists() or not (persist_dir / "chroma.sqlite3").exists():
        logger.warning(
            f"Chroma 索引不存在: {persist_dir}，请先运行 "
            "python src/preprocessing/build_schema_index.py"
        )
        vector_store = None
    else:
        vector_store = VectorStoreManager(
            collection_name="nl2sql_columns",
            persist_directory=str(persist_dir),
        )
        stats = vector_store.get_stats()
        logger.info(f"VectorStore 加载完成: {stats.get('total_embeddings', 0)} 条")

    # 3. LLM 客户端
    llm_client = LLMClient(model=model_name)
    logger.info(f"LLMClient 初始化: {model_name}")

    # 4. 与 db 无关的 Agent
    generator = SQLGenerator(llm_client=llm_client, num_candidates=3)
    result_verifier = ResultVerifier(llm_client=llm_client, strictness="strict")
    decider = SelfConsistencyDecision(llm_client=llm_client, result_verifier=result_verifier)
    answerability_checker = AnswerabilityChecker(llm_client=llm_client, strictness="loose")
    history_cache = HistoryCache(llm_client=llm_client, min_confidence=0.8)
    memory_updater = MemoryUpdater(llm_client=llm_client)

    # 5. 会话管理器（文件存储 + 进程 LRU）
    _session_manager = SessionManager(
        base_dir=str(Path(data_dir) / "sessions"),
        max_cache_size=200,
    )

    # 6. 装配 Globals
    _globals = Globals(
        bge_vectorizer=vectorizer,
        vector_store=vector_store,
        llm_client=llm_client,
        generator=generator,
        decider=decider,
        answerability_checker=answerability_checker,
        history_cache=history_cache,
        memory_updater=memory_updater,
        data_dir=data_dir,
    )

    # 7. DbContextPool
    _db_pool = DbContextPool(max_size=pool_max_size, globals_=_globals)
    logger.info(f"DbContextPool 初始化: max_size={pool_max_size}")


def shutdown_globals() -> None:
    """lifespan shutdown 钩子：释放所有 DbContext"""
    global _db_pool, _globals, _session_manager
    if _db_pool is not None:
        _db_pool.close_all()
    _db_pool = None
    _globals = None
    _session_manager = None
    _user_memory_cache.clear()


# ---------------------------------------------------------------------------
# FastAPI 依赖项
# ---------------------------------------------------------------------------

def get_globals() -> Globals:
    if _globals is None:
        raise RuntimeError("Globals 未初始化，请先调用 init_globals()")
    return _globals


def get_db_pool() -> DbContextPool:
    if _db_pool is None:
        raise RuntimeError("DbContextPool 未初始化，请先调用 init_globals()")
    return _db_pool


def get_session_manager() -> SessionManager:
    if _session_manager is None:
        raise RuntimeError("SessionManager 未初始化，请先调用 init_globals()")
    return _session_manager


def get_user_memory(user_id: str) -> UserMemory:
    """懒加载 UserMemory（进程 LRU 缓存，容量上限 100）"""
    if user_id in _user_memory_cache:
        return _user_memory_cache[user_id]

    if _globals is None:
        raise RuntimeError("Globals 未初始化")

    base_dir = str(Path(_globals.data_dir) / "user_memory")
    um = UserMemory(user_id=user_id, base_dir=base_dir)
    um.load()

    if len(_user_memory_cache) >= _USER_MEMORY_CACHE_MAX:
        oldest_key = next(iter(_user_memory_cache))
        del _user_memory_cache[oldest_key]

    _user_memory_cache[user_id] = um
    return um

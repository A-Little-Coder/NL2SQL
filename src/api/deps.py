"""API 依赖注入

提供：
- get_session_manager() → 单例 SessionManager
- get_user_memory(user_id) → 懒加载 UserMemory（进程级 LRU 缓存）
- get_graph() → 预编译的 LangGraph 主图
"""

from functools import lru_cache
from typing import Dict

from src.graph.main_graph import build_main_graph
from src.memory.session_manager import SessionManager
from src.memory.user_memory import UserMemory


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

_session_manager: SessionManager = None
_graph = None


def init_components(
    retriever=None,
    selector=None,
    generator=None,
    fix_loop=None,
    decider=None,
    *,
    answerability_checker=None,
    history_cache=None,
    memory_updater=None,
    enable_clarification: bool = False,
    session_base_dir: str = "data/sessions",
    user_memory_base_dir: str = "data/user_memory",
):
    """
    一次性初始化所有组件

    在 FastAPI lifespan 中调用。
    """
    global _session_manager, _graph

    _session_manager = SessionManager(base_dir=session_base_dir, max_cache_size=200)

    _graph = build_main_graph(
        retriever=retriever,
        selector=selector,
        generator=generator,
        fix_loop=fix_loop,
        decider=decider,
        answerability_checker=answerability_checker,
        enable_clarification=enable_clarification,
        history_cache=history_cache,
        memory_updater=memory_updater,
    )


def get_session_manager() -> SessionManager:
    if _session_manager is None:
        raise RuntimeError("组件未初始化，请先调用 init_components()")
    return _session_manager


def get_graph():
    if _graph is None:
        raise RuntimeError("组件未初始化，请先调用 init_components()")
    return _graph


# ---------------------------------------------------------------------------
# UserMemory 懒加载缓存（进程级 LRU）
# ---------------------------------------------------------------------------

_user_memory_cache: Dict[str, UserMemory] = {}
_USER_MEMORY_CACHE_MAX = 100


def get_user_memory(user_id: str) -> UserMemory:
    """懒加载 UserMemory（带 LRU 缓存）"""
    if user_id in _user_memory_cache:
        return _user_memory_cache[user_id]

    um = UserMemory(user_id=user_id)
    um.load()

    # LRU 淘汰
    if len(_user_memory_cache) >= _USER_MEMORY_CACHE_MAX:
        oldest_key = next(iter(_user_memory_cache))
        del _user_memory_cache[oldest_key]

    _user_memory_cache[user_id] = um
    return um

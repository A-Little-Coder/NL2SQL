"""
DbContext + DbContextPool（决策 49）

每个 db_id 对应一个 DbContext，持有该数据库独有的资源：
    - DatabaseConnector
    - LSHIndexer
    - InformationRetrieval（持有 BGE / VectorStore 的全局引用）
    - SchemaSelector
    - SQLExecutor
    - SQLFixLoop
    - CompiledGraph

全局共享单例（BGE-M3、VectorStore、LLM、Generator、Decider 等）通过 `globals_` 传入。

DbContextPool 采用 LRU + 引用计数：
    - acquire(db_id) → 命中则 move_to_end；未命中则构造（必要时淘汰最久未用且 refcount=0 的）
    - release(db_id) → refcount -= 1
    - 全部在用时允许池短暂超 max，不阻塞请求
"""

import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from src.execution.executor import SQLExecutor, SQLFixLoop
from src.graph.main_graph import build_main_graph
from src.preprocessing.database_connector import DatabaseConnector
from src.preprocessing.lsh_index import LSHIndexer
from src.retrieval.information_retrieval import InformationRetrieval
from src.schema_selection.schema_selector import SchemaSelector


@dataclass
class DbContext:
    """单个数据库的运行时上下文"""

    db_id: str
    db_path: str
    connector: DatabaseConnector
    lsh_indexer: Optional[LSHIndexer]
    retriever: InformationRetrieval
    selector: SchemaSelector
    executor: SQLExecutor
    fix_loop: SQLFixLoop
    graph: Any  # CompiledGraph
    refcount: int = 0

    def close(self):
        """释放数据库连接（不影响全局共享组件）"""
        try:
            self.connector.disconnect()
        except Exception as e:
            logger.warning(f"DbContext({self.db_id}) connector.disconnect 异常: {e}")


@dataclass
class Globals:
    """启动时构造的全局共享单例集合"""

    bge_vectorizer: Any
    vector_store: Any
    llm_client: Any
    generator: Any
    decider: Any
    answerability_checker: Any
    history_cache: Any
    memory_updater: Any
    data_dir: str  # data/ 根目录，用于定位 db 文件


def _prepare_lsh_indexer(db_directory: str) -> Optional[LSHIndexer]:
    """加载指定 db 目录的 LSH 索引"""
    indexer = LSHIndexer(signature_size=128, n_gram=3, threshold=0.3)
    if not LSHIndexer.is_lsh_built(db_directory):
        logger.warning(f"未找到 LSH 索引: {db_directory}")
        return None
    try:
        lsh, minhashes = LSHIndexer.load_db_lsh(db_directory)
        indexer._loaded_lsh = lsh
        indexer._loaded_minhashes = minhashes
        logger.info(f"LSH 已加载: {len(minhashes)} 条 ({db_directory})")
        return indexer
    except Exception as e:
        logger.error(f"LSH 加载失败 ({db_directory}): {e}")
        return None


def _find_db_path(data_dir: str, db_id: str) -> str:
    """在 data_dir 下定位指定 db_id 的 sqlite 文件"""
    db_dir = Path(data_dir) / db_id
    if not db_dir.exists():
        raise FileNotFoundError(f"未找到数据库目录: {db_dir}")
    for ext in (".sqlite", ".db"):
        candidate = db_dir / f"{db_id}{ext}"
        if candidate.exists():
            return str(candidate)
    for f in db_dir.glob("*.sqlite"):
        return str(f)
    for f in db_dir.glob("*.db"):
        return str(f)
    raise FileNotFoundError(f"未在 {db_dir} 找到 sqlite/db 文件")


class DbContextPool:
    """按 db_id 缓存 DbContext 的 LRU 池

    - 进程内单例（仅在单 worker 部署下使用）
    - 用 RLock 保护 OrderedDict 操作
    - 引用计数防止活跃 ctx 被淘汰
    """

    def __init__(self, max_size: int, globals_: Globals):
        self.max_size = max(1, int(max_size))
        self.globals = globals_
        self._cache: "OrderedDict[str, DbContext]" = OrderedDict()
        self._lock = threading.RLock()

    # ── 公共 API ─────────────────────────────────────────────

    def acquire(self, db_id: str) -> DbContext:
        """获取 DbContext，refcount += 1（必须配 release 使用）"""
        with self._lock:
            if db_id in self._cache:
                self._cache.move_to_end(db_id)
                ctx = self._cache[db_id]
                ctx.refcount += 1
                return ctx

            # 未命中：先尝试淘汰，再构造
            self._evict_if_needed()
            ctx = self._build(db_id)
            self._cache[db_id] = ctx
            ctx.refcount = 1
            return ctx

    def release(self, db_id: str) -> None:
        """释放引用，refcount -= 1"""
        with self._lock:
            ctx = self._cache.get(db_id)
            if ctx is None:
                return
            if ctx.refcount > 0:
                ctx.refcount -= 1

    def peek(self, db_id: str) -> Optional[DbContext]:
        """只读窥探（不影响 refcount 和 LRU 顺序）"""
        with self._lock:
            return self._cache.get(db_id)

    def stats(self) -> dict:
        """返回池状态摘要（用于 /health）"""
        with self._lock:
            return {
                "max": self.max_size,
                "size": len(self._cache),
                "cached": [
                    {"db_id": k, "refcount": v.refcount}
                    for k, v in self._cache.items()
                ],
            }

    def warm_up(self, db_id: str) -> None:
        """预加载某 db（构造后立即 release，保留在缓存中）"""
        ctx = self.acquire(db_id)
        self.release(db_id)
        logger.info(f"warm-up 完成: db_id={db_id}")

    def close_all(self) -> None:
        """关闭所有缓存的 DbContext（shutdown 用）"""
        with self._lock:
            for db_id, ctx in list(self._cache.items()):
                logger.info(f"关闭 DbContext: {db_id}")
                ctx.close()
            self._cache.clear()

    # ── 内部 ─────────────────────────────────────────────────

    def _evict_if_needed(self) -> None:
        """池满时淘汰最久未用且 refcount=0 的 ctx；全部在用则跳过"""
        if len(self._cache) < self.max_size:
            return
        for db_id, ctx in list(self._cache.items()):
            if ctx.refcount == 0:
                logger.info(f"LRU 淘汰 DbContext: {db_id}")
                self._cache.pop(db_id)
                ctx.close()
                return
        logger.warning(
            f"池已满 ({len(self._cache)}/{self.max_size}) 且所有 ctx 都在使用中，"
            "本次允许短暂超 max"
        )

    def _build(self, db_id: str) -> DbContext:
        """构造一个新的 DbContext"""
        g = self.globals
        db_path = _find_db_path(g.data_dir, db_id)
        db_directory = str(Path(db_path).parent)

        logger.info(f"构造 DbContext: db_id={db_id}, path={db_path}")

        connector = DatabaseConnector(db_path, db_type="sqlite")
        lsh_indexer = _prepare_lsh_indexer(db_directory)

        retriever = InformationRetrieval(
            llm_client=g.llm_client,
            lsh_indexer=lsh_indexer,
            vector_store=g.vector_store,
        )
        if g.bge_vectorizer is not None:
            retriever._vectorizer = g.bge_vectorizer

        selector = SchemaSelector(llm_client=g.llm_client, db_connector=connector)
        executor = SQLExecutor(db_connector=connector)
        fix_loop = SQLFixLoop(executor=executor, llm_client=g.llm_client, max_retries=2)

        graph = build_main_graph(
            retriever=retriever,
            selector=selector,
            generator=g.generator,
            fix_loop=fix_loop,
            decider=g.decider,
            answerability_checker=g.answerability_checker,
            history_cache=g.history_cache,
            memory_updater=g.memory_updater,
            enable_clarification=False,
        )

        return DbContext(
            db_id=db_id,
            db_path=db_path,
            connector=connector,
            lsh_indexer=lsh_indexer,
            retriever=retriever,
            selector=selector,
            executor=executor,
            fix_loop=fix_loop,
            graph=graph,
        )

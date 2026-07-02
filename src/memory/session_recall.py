"""
SessionMemory v2 混合召回组件

本模块只负责会话级历史 query 的轻量召回：
- 召回范围必须限定在当前 user_id / session_id / db_id / success=true
- demo 阶段 query 向量索引使用 Chroma，完整历史对话使用 JSON
- BM25 与 RRF 使用本地实现，避免额外依赖
- 召回结果只作为 HistoryCache 候选，不直接决定 SQL 复用
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Protocol, Tuple

from loguru import logger

from src.memory.storage import Storage


# ---------------------------------------------------------------------------
# 配置与数据结构
# ---------------------------------------------------------------------------


@dataclass
class SessionRecallConfig:
    """SessionMemory 混合召回配置"""

    dense_top_k: int = 10
    bm25_top_k: int = 10
    rrf_k: int = 60
    rrf_threshold: float = 0.015
    require_multi_channel_hit: bool = False
    collection_name: str = "nl2sql_session_queries"


@dataclass
class SessionQueryMemory:
    """可写入召回库的一轮成功查询"""

    historical_query: str
    historical_sql: str
    user_id: str
    session_id: str
    db_id: str
    conversation_id: str
    turn_id: int
    success: bool = True
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @property
    def memory_id(self) -> str:
        return f"{self.user_id}:{self.session_id}:{self.db_id}:{self.turn_id}"

    def to_metadata(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "session_id": self.session_id,
            "db_id": self.db_id,
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "success": self.success,
            "final_sql": self.historical_sql,
            "created_at": self.created_at,
        }


@dataclass
class HistoricalSQLReference:
    """历史 SQL 弱参考 / HistoryCache 候选"""

    historical_query: str
    historical_sql: str
    rrf_score: float
    dense_rank: Optional[int]
    bm25_rank: Optional[int]
    conversation_id: str
    turn_id: int
    user_id: str = ""
    session_id: str = ""
    db_id: str = ""
    source: str = "session_memory"

    def to_turn(self) -> Dict[str, Any]:
        """转成 HistoryCache 兼容的历史轮次格式"""
        return {
            "turn_index": self.turn_id,
            "user_query": self.historical_query,
            "final_sql": self.historical_sql,
            "rrf_score": self.rrf_score,
            "dense_rank": self.dense_rank,
            "bm25_rank": self.bm25_rank,
            "conversation_id": self.conversation_id,
            "source": self.source,
        }

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Query recall index 抽象与 Chroma 实现
# ---------------------------------------------------------------------------


class QueryRecallIndex(Protocol):
    """query recall index 抽象"""

    def upsert(self, memory: SessionQueryMemory) -> bool:
        ...

    def query_dense(
        self,
        query: str,
        *,
        user_id: str,
        session_id: str,
        db_id: str,
        top_k: int,
    ) -> List[Dict[str, Any]]:
        ...


class ChromaSessionQueryIndex:
    """demo 版 Chroma query recall index"""

    def __init__(
        self,
        vectorizer,
        persist_directory: str,
        collection_name: str = "nl2sql_session_queries",
    ):
        self.vectorizer = vectorizer
        self.persist_directory = persist_directory
        self.collection_name = collection_name
        self._store = None

    @property
    def store(self):
        if self._store is None:
            from src.preprocessing.vector_store import VectorStoreManager

            self._store = VectorStoreManager(
                collection_name=self.collection_name,
                persist_directory=self.persist_directory,
            )
        return self._store

    def _embed(self, text: str) -> List[float]:
        if self.vectorizer is None:
            raise RuntimeError("Session query vectorizer 未配置")
        embeddings = self.vectorizer.embed_texts([text], return_dense=True)
        dense = embeddings.get("dense", [])
        if not dense:
            raise RuntimeError("Session query embedding 为空")
        return dense[0]

    def upsert(self, memory: SessionQueryMemory) -> bool:
        if not memory.success:
            return False
        vector = self._embed(memory.historical_query)
        return self.store.add_embeddings([
            {
                "id": memory.memory_id,
                "embedding": vector,
                "metadata": memory.to_metadata(),
                "document": memory.historical_query,
            }
        ])

    def query_dense(
        self,
        query: str,
        *,
        user_id: str,
        session_id: str,
        db_id: str,
        top_k: int,
    ) -> List[Dict[str, Any]]:
        vector = self._embed(query)
        where = {
            "$and": [
                {"user_id": {"$eq": user_id}},
                {"session_id": {"$eq": session_id}},
                {"db_id": {"$eq": db_id}},
                {"success": {"$eq": True}},
            ]
        }
        results = self.store.query(vector, n_results=top_k, where_filter=where)
        for idx, item in enumerate(results, start=1):
            item["dense_rank"] = idx
        return results


# ---------------------------------------------------------------------------
# Conversation Store 抽象与 JSON 实现
# ---------------------------------------------------------------------------


class ConversationStore(Protocol):
    """无结果历史对话存储抽象"""

    def upsert_turn(self, memory: SessionQueryMemory) -> bool:
        ...

    def load_turn_window(
        self,
        *,
        user_id: str,
        session_id: str,
        conversation_id: str,
        turn_id: int,
        window: int = 1,
    ) -> List[Dict[str, Any]]:
        ...

    def list_turns(self, *, user_id: str, session_id: str, db_id: str) -> List[Dict[str, Any]]:
        ...


class JsonConversationStore:
    """demo 版 JSON conversation store，不保存结果数据和中间 state"""

    def __init__(self, base_dir: str):
        self._storage = Storage(base_dir)

    def _path(self, user_id: str, session_id: str) -> Path:
        return self._storage.session_path(user_id, session_id)

    @staticmethod
    def _empty(user_id: str, session_id: str) -> Dict[str, Any]:
        now = datetime.now().isoformat(timespec="seconds")
        return {
            "user_id": user_id,
            "session_id": session_id,
            "created_at": now,
            "updated_at": now,
            "turns": [],
        }

    def upsert_turn(self, memory: SessionQueryMemory) -> bool:
        path = self._path(memory.user_id, memory.session_id)
        data = self._storage.atomic_read(path) or self._empty(memory.user_id, memory.session_id)
        turn = {
            "conversation_id": memory.conversation_id,
            "turn_id": memory.turn_id,
            "user_id": memory.user_id,
            "session_id": memory.session_id,
            "db_id": memory.db_id,
            "user_query": memory.historical_query,
            "final_sql": memory.historical_sql,
            "success": memory.success,
            "timestamp": memory.created_at,
        }
        turns = [t for t in data.get("turns", []) if int(t.get("turn_id", -1)) != memory.turn_id]
        turns.append(turn)
        turns.sort(key=lambda t: int(t.get("turn_id", 0)))
        data["turns"] = turns
        data["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self._storage.atomic_write(path, data)
        return True

    def load_turn_window(
        self,
        *,
        user_id: str,
        session_id: str,
        conversation_id: str,
        turn_id: int,
        window: int = 1,
    ) -> List[Dict[str, Any]]:
        data = self._storage.atomic_read(self._path(user_id, session_id)) or {}
        turns = [
            t for t in data.get("turns", [])
            if t.get("conversation_id") == conversation_id
        ]
        if not turns:
            return []
        lower = turn_id - window
        upper = turn_id + window
        return [
            t for t in turns
            if lower <= int(t.get("turn_id", -1)) <= upper and t.get("success") is True
        ]

    def list_turns(self, *, user_id: str, session_id: str, db_id: str) -> List[Dict[str, Any]]:
        data = self._storage.atomic_read(self._path(user_id, session_id)) or {}
        return [
            t for t in data.get("turns", [])
            if t.get("user_id") == user_id
            and t.get("session_id") == session_id
            and t.get("db_id") == db_id
            and t.get("success") is True
        ]


# ---------------------------------------------------------------------------
# BM25 与 RRF
# ---------------------------------------------------------------------------


_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[一-鿿]")


def tokenize_for_bm25(text: str) -> List[str]:
    """中英文轻量 tokenizer：英文词 + 中文 unigram/bigram"""
    raw = _TOKEN_RE.findall((text or "").lower())
    tokens: List[str] = []
    zh_chars: List[str] = []
    for tok in raw:
        if re.fullmatch(r"[一-鿿]", tok):
            zh_chars.append(tok)
            tokens.append(tok)
        else:
            tokens.append(tok)
    tokens.extend("".join(zh_chars[i:i + 2]) for i in range(max(len(zh_chars) - 1, 0)))
    return [t for t in tokens if t]


class LocalBM25Retriever:
    """本地轻量 BM25，数据来源为 conversation store 的当前 session 成功 turns"""

    def __init__(self, conversation_store: ConversationStore, k1: float = 1.5, b: float = 0.75):
        self.conversation_store = conversation_store
        self.k1 = k1
        self.b = b

    def query(
        self,
        query: str,
        *,
        user_id: str,
        session_id: str,
        db_id: str,
        top_k: int,
    ) -> List[Dict[str, Any]]:
        docs = self.conversation_store.list_turns(user_id=user_id, session_id=session_id, db_id=db_id)
        if not docs:
            return []
        query_tokens = tokenize_for_bm25(query)
        if not query_tokens:
            return []

        doc_tokens = [tokenize_for_bm25(d.get("user_query", "")) for d in docs]
        avgdl = sum(len(toks) for toks in doc_tokens) / max(len(doc_tokens), 1)
        df: Dict[str, int] = {}
        for toks in doc_tokens:
            for tok in set(toks):
                df[tok] = df.get(tok, 0) + 1

        scored: List[Tuple[float, Dict[str, Any]]] = []
        n_docs = len(docs)
        for doc, toks in zip(docs, doc_tokens):
            if not toks:
                continue
            tf: Dict[str, int] = {}
            for tok in toks:
                tf[tok] = tf.get(tok, 0) + 1
            score = 0.0
            for tok in query_tokens:
                if tok not in tf:
                    continue
                idf = math.log(1 + (n_docs - df.get(tok, 0) + 0.5) / (df.get(tok, 0) + 0.5))
                freq = tf[tok]
                denom = freq + self.k1 * (1 - self.b + self.b * len(toks) / max(avgdl, 1e-9))
                score += idf * (freq * (self.k1 + 1) / denom)
            if score > 0:
                item = {
                    "id": f"{doc.get('user_id')}:{doc.get('session_id')}:{doc.get('db_id')}:{doc.get('turn_id')}",
                    "document": doc.get("user_query", ""),
                    "metadata": {
                        "user_id": doc.get("user_id"),
                        "session_id": doc.get("session_id"),
                        "db_id": doc.get("db_id"),
                        "conversation_id": doc.get("conversation_id"),
                        "turn_id": doc.get("turn_id"),
                        "success": doc.get("success"),
                        "final_sql": doc.get("final_sql", ""),
                        "created_at": doc.get("timestamp", ""),
                    },
                    "bm25_score": score,
                }
                scored.append((score, item))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = [item for _, item in scored[:top_k]]
        for idx, item in enumerate(results, start=1):
            item["bm25_rank"] = idx
        return results


class RRFRanker:
    """Reciprocal Rank Fusion 排序器"""

    def __init__(self, k: int = 60, threshold: float = 0.015, require_multi_channel_hit: bool = False):
        self.k = k
        self.threshold = threshold
        self.require_multi_channel_hit = require_multi_channel_hit

    def fuse(
        self,
        dense_results: Iterable[Dict[str, Any]],
        bm25_results: Iterable[Dict[str, Any]],
    ) -> List[HistoricalSQLReference]:
        merged: Dict[str, Dict[str, Any]] = {}

        def add(item: Dict[str, Any], channel: str):
            key = item.get("id")
            if not key:
                return
            entry = merged.setdefault(key, {"item": item, "dense_rank": None, "bm25_rank": None})
            entry["item"] = {**entry.get("item", {}), **item}
            if channel == "dense":
                entry["dense_rank"] = item.get("dense_rank")
            else:
                entry["bm25_rank"] = item.get("bm25_rank")

        for item in dense_results:
            add(item, "dense")
        for item in bm25_results:
            add(item, "bm25")

        refs: List[HistoricalSQLReference] = []
        for entry in merged.values():
            dense_rank = entry.get("dense_rank")
            bm25_rank = entry.get("bm25_rank")
            if self.require_multi_channel_hit and (dense_rank is None or bm25_rank is None):
                continue
            score = 0.0
            if dense_rank is not None:
                score += 1.0 / (self.k + int(dense_rank))
            if bm25_rank is not None:
                score += 1.0 / (self.k + int(bm25_rank))
            if score < self.threshold:
                continue
            item = entry.get("item", {})
            meta = item.get("metadata", {}) or {}
            refs.append(HistoricalSQLReference(
                historical_query=item.get("document", ""),
                historical_sql=meta.get("final_sql", ""),
                rrf_score=score,
                dense_rank=dense_rank,
                bm25_rank=bm25_rank,
                conversation_id=meta.get("conversation_id", ""),
                turn_id=int(meta.get("turn_id", 0) or 0),
                user_id=meta.get("user_id", ""),
                session_id=meta.get("session_id", ""),
                db_id=meta.get("db_id", ""),
            ))
        refs.sort(key=lambda r: r.rrf_score, reverse=True)
        return refs


# ---------------------------------------------------------------------------
# HybridSessionRetriever
# ---------------------------------------------------------------------------


class HybridSessionRetriever:
    """SessionMemory v2 混合召回器"""

    def __init__(
        self,
        query_index: QueryRecallIndex,
        conversation_store: ConversationStore,
        config: Optional[SessionRecallConfig] = None,
        bm25_retriever: Optional[LocalBM25Retriever] = None,
        rrf_ranker: Optional[RRFRanker] = None,
    ):
        self.query_index = query_index
        self.conversation_store = conversation_store
        self.config = config or SessionRecallConfig()
        self.bm25_retriever = bm25_retriever or LocalBM25Retriever(conversation_store)
        self.rrf_ranker = rrf_ranker or RRFRanker(
            k=self.config.rrf_k,
            threshold=self.config.rrf_threshold,
            require_multi_channel_hit=self.config.require_multi_channel_hit,
        )

    def retrieve(
        self,
        query: str,
        *,
        user_id: str,
        session_id: str,
        db_id: str,
    ) -> List[HistoricalSQLReference]:
        """在当前 session 内执行 dense + BM25 + RRF 召回"""
        try:
            dense = self.query_index.query_dense(
                query,
                user_id=user_id,
                session_id=session_id,
                db_id=db_id,
                top_k=self.config.dense_top_k,
            )
        except Exception as e:
            logger.warning(f"Session dense recall 失败，降级为空: {e}")
            dense = []

        try:
            bm25 = self.bm25_retriever.query(
                query,
                user_id=user_id,
                session_id=session_id,
                db_id=db_id,
                top_k=self.config.bm25_top_k,
            )
        except Exception as e:
            logger.warning(f"Session BM25 recall 失败，降级为空: {e}")
            bm25 = []

        refs = self.rrf_ranker.fuse(dense, bm25)
        hydrated: List[HistoricalSQLReference] = []
        for ref in refs:
            try:
                window = self.conversation_store.load_turn_window(
                    user_id=user_id,
                    session_id=session_id,
                    conversation_id=ref.conversation_id,
                    turn_id=ref.turn_id,
                    window=1,
                )
                # 回表成功后，优先用命中 turn 的标准化 query/sql，避免 Chroma metadata 不完整
                for turn in window:
                    if int(turn.get("turn_id", -1)) == ref.turn_id:
                        ref.historical_query = turn.get("user_query", ref.historical_query)
                        ref.historical_sql = turn.get("final_sql", ref.historical_sql)
                        break
            except Exception as e:
                logger.warning(f"Conversation store 回表失败，保留索引元数据: {e}")
            hydrated.append(ref)
        return hydrated

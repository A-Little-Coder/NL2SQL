"""SessionMemory v2 混合召回测试"""

from src.memory.session_recall import (
    HistoricalSQLReference,
    HybridSessionRetriever,
    JsonConversationStore,
    LocalBM25Retriever,
    RRFRanker,
    SessionQueryMemory,
    SessionRecallConfig,
)


class FakeQueryIndex:
    def __init__(self):
        self.memories = []
        self.fail = False

    def upsert(self, memory):
        if memory.success:
            self.memories.append(memory)
            return True
        return False

    def query_dense(self, query, *, user_id, session_id, db_id, top_k):
        if self.fail:
            raise RuntimeError("dense failed")
        rows = []
        for memory in self.memories:
            if (
                memory.user_id == user_id
                and memory.session_id == session_id
                and memory.db_id == db_id
                and memory.success
            ):
                rows.append({
                    "id": memory.memory_id,
                    "document": memory.historical_query,
                    "metadata": memory.to_metadata(),
                })
        rows = rows[:top_k]
        for idx, row in enumerate(rows, start=1):
            row["dense_rank"] = idx
        return rows


def _memory(query="查询苹果销售额", sql="SELECT SUM(amount) FROM sales", session_id="s1", turn_id=1):
    return SessionQueryMemory(
        historical_query=query,
        historical_sql=sql,
        user_id="u1",
        session_id=session_id,
        db_id="db1",
        conversation_id=session_id,
        turn_id=turn_id,
        success=True,
    )


def test_rrf_supports_single_and_dual_channel_hits():
    ranker = RRFRanker(k=60, threshold=0.0, require_multi_channel_hit=False)
    dense = [
        {"id": "a", "document": "A", "metadata": {"final_sql": "SQL A", "turn_id": 1}, "dense_rank": 1},
        {"id": "b", "document": "B", "metadata": {"final_sql": "SQL B", "turn_id": 2}, "dense_rank": 2},
    ]
    bm25 = [
        {"id": "a", "document": "A", "metadata": {"final_sql": "SQL A", "turn_id": 1}, "bm25_rank": 1},
        {"id": "c", "document": "C", "metadata": {"final_sql": "SQL C", "turn_id": 3}, "bm25_rank": 1},
    ]

    refs = ranker.fuse(dense, bm25)

    assert {r.historical_query for r in refs} == {"A", "B", "C"}
    both = next(r for r in refs if r.historical_query == "A")
    assert both.dense_rank == 1
    assert both.bm25_rank == 1
    assert both.rrf_score > next(r for r in refs if r.historical_query == "B").rrf_score


def test_rrf_threshold_filters_low_scores():
    ranker = RRFRanker(k=60, threshold=0.02, require_multi_channel_hit=False)
    refs = ranker.fuse([
        {"id": "a", "document": "A", "metadata": {"final_sql": "SQL A", "turn_id": 1}, "dense_rank": 1}
    ], [])

    assert refs == []


def test_json_conversation_store_excludes_result_data(tmp_path):
    store = JsonConversationStore(str(tmp_path / "session_memory_v2"))
    memory = _memory()

    store.upsert_turn(memory)
    turns = store.load_turn_window(
        user_id="u1",
        session_id="s1",
        conversation_id="s1",
        turn_id=1,
    )

    assert turns[0]["user_query"] == "查询苹果销售额"
    assert turns[0]["final_sql"] == "SELECT SUM(amount) FROM sales"
    assert "final_result" not in turns[0]
    assert "graph_state" not in turns[0]


def test_bm25_filters_current_session_only(tmp_path):
    store = JsonConversationStore(str(tmp_path / "session_memory_v2"))
    store.upsert_turn(_memory(query="查询苹果销售额", session_id="s1", turn_id=1))
    store.upsert_turn(_memory(query="查询苹果销售额", session_id="s2", turn_id=1))
    bm25 = LocalBM25Retriever(store)

    rows = bm25.query("苹果销售额", user_id="u1", session_id="s1", db_id="db1", top_k=10)

    assert len(rows) == 1
    assert rows[0]["metadata"]["session_id"] == "s1"


def test_hybrid_retriever_reads_conversation_store_after_rrf(tmp_path):
    index = FakeQueryIndex()
    store = JsonConversationStore(str(tmp_path / "session_memory_v2"))
    memory = _memory()
    index.upsert(memory)
    store.upsert_turn(memory)
    retriever = HybridSessionRetriever(
        query_index=index,
        conversation_store=store,
        config=SessionRecallConfig(rrf_threshold=0.0),
    )

    refs = retriever.retrieve("苹果销售额", user_id="u1", session_id="s1", db_id="db1")

    assert len(refs) == 1
    assert isinstance(refs[0], HistoricalSQLReference)
    assert refs[0].historical_query == "查询苹果销售额"
    assert refs[0].historical_sql == "SELECT SUM(amount) FROM sales"


def test_hybrid_retriever_degrades_when_dense_fails(tmp_path):
    index = FakeQueryIndex()
    index.fail = True
    store = JsonConversationStore(str(tmp_path / "session_memory_v2"))
    memory = _memory()
    store.upsert_turn(memory)
    retriever = HybridSessionRetriever(
        query_index=index,
        conversation_store=store,
        config=SessionRecallConfig(rrf_threshold=0.0),
    )

    refs = retriever.retrieve("苹果销售额", user_id="u1", session_id="s1", db_id="db1")

    assert len(refs) == 1
    assert refs[0].bm25_rank == 1

# ============================================================================
# NL2SQL 端到端真实试用脚本（含会话记忆 + 用户记忆 + 主图完整链路）
# ============================================================================
# 调用真实 Qwen API，使用 BIRD-SQL 数据集中的 SQLite 数据库
# 与 e2e_live.py 的区别：
#   1. 直接走 graph.invoke()，不再手动跑每个步骤
#   2. 注入 SessionMemory（会话级，跨轮持久化）和 UserMemory（用户级，跨会话持久化）
#   3. 加入 HistoryCache（决策 30，历史命中检测）和 MemoryUpdater（决策 29，记忆自动学习）
#   4. 验证 follow-up 查询（"那去年的呢"会读会话历史）
#   5. 验证记忆持久化（关闭后重启依然能命中）
#
# 使用前请先运行索引构建:
#   python src/preprocessing/build_lsh_index.py --db_id california_schools
#   python src/preprocessing/build_schema_index.py --db_id california_schools
#
# 用法:
#   python tests/e2e_live_with_memory.py
#
# 交互命令:
#   /new        — 开启一个新会话（生成新的 session_id）
#   /sessions   — 列出当前用户的所有会话
#   /switch     — 切换数据库
#   /memory     — 查看当前用户的记忆摘要
#   /history    — 查看当前会话的历史轮次
#   /clear      — 清空当前会话（删除会话文件）
#   quit / q    — 退出
# ============================================================================


import os
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    os.system("chcp 65001 > nul 2>&1")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from loguru import logger

from src.preprocessing.database_connector import DatabaseConnector
from src.preprocessing.lsh_index import LSHIndexer
from src.preprocessing.schema_vectorizer import SchemaVectorizer
from src.preprocessing.vector_store import VectorStoreManager
from src.retrieval.information_retrieval import InformationRetrieval
from src.schema_selection.schema_selector import SchemaSelector
from src.sql_generation.sql_generator import SQLGenerator
from src.execution.executor import SQLExecutor, SQLFixLoop
from src.decision.self_consistency import SelfConsistencyDecision
from src.verification.answerability import AnswerabilityChecker
from src.verification.result_verifier import ResultVerifier
from src.memory.history_cache import HistoryCache
from src.memory.memory_updater import MemoryUpdater
from src.memory.session_manager import SessionManager
from src.memory.user_memory import UserMemory
from src.graph import build_main_graph, create_initial_state
from utils.llm_client import LLMClient


# ============================================================================
# 辅助函数（与 e2e_live.py 一致）
# ============================================================================

def find_bird_databases(data_dir: str = None) -> dict:
    if data_dir is None:
        data_dir = str(Path(__file__).parent.parent / "data")
    databases = {}
    data_path = Path(data_dir)
    if not data_path.exists():
        return databases
    for entry in sorted(data_path.iterdir()):
        if not entry.is_dir():
            continue
        for ext in [".sqlite", ".db"]:
            for f in entry.glob(f"{entry.name}{ext}"):
                databases[entry.name] = str(f)
                break
        if entry.name not in databases:
            for f in entry.glob("*.sqlite"):
                databases[entry.name] = str(f)
                break
    return databases


def prepare_lsh_indexer(db_directory: str, lsh_threshold: float = 0.3,
                        signature_size: int = 128, n_gram: int = 3) -> LSHIndexer:
    indexer = LSHIndexer(signature_size=signature_size, n_gram=n_gram,
                         threshold=lsh_threshold)
    if not LSHIndexer.is_lsh_built(db_directory):
        logger.error(f"未找到 LSH 索引：{db_directory}")
        logger.error(f"请先运行：python src/preprocessing/build_lsh_index.py --db_id {Path(db_directory).name}")
        return None
    try:
        lsh, minhashes = LSHIndexer.load_db_lsh(db_directory)
        indexer._loaded_lsh = lsh
        indexer._loaded_minhashes = minhashes
        logger.info(f"LSH 索引已加载: {len(minhashes)} 条")
        return indexer
    except Exception as e:
        logger.error(f"LSH 加载失败: {e}")
        return None


def prepare_schema_index(data_dir: str, bge_model_path: str) -> tuple:
    persist_dir = Path(data_dir) / "preprocessed" / "chroma"
    collection_name = "nl2sql_columns"

    if not persist_dir.exists() or not (persist_dir / "chroma.sqlite3").exists():
        logger.error(f"未找到 schema 向量索引：{persist_dir}")
        logger.error("请先运行：python src/preprocessing/build_schema_index.py")
        return None, None

    try:
        vectorizer = SchemaVectorizer(model_name=bge_model_path, device="cpu")
        vectorizer.load_model()
        vector_store = VectorStoreManager(
            collection_name=collection_name,
            persist_directory=str(persist_dir),
        )
        stats = vector_store.get_stats()
        logger.info(f"Schema 索引已加载: {stats.get('total_embeddings', 0)} 条")
        return vectorizer, vector_store
    except Exception as e:
        logger.error(f"Schema 索引加载失败: {e}")
        return None, None


def print_result_table(rows, headers=None):
    if not rows:
        print("  (无结果)")
        return
    if headers is None:
        if hasattr(rows[0], "keys"):
            headers = list(rows[0].keys())
        else:
            headers = [f"col{i}" for i in range(len(rows[0]))]

    widths = [len(str(h)) for h in headers]
    for row in rows[:50]:
        for i, cell in enumerate(row):
            if i < len(widths):
                s = str(cell)
                if len(s) > 40:
                    s = s[:37] + "..."
                widths[i] = max(widths[i], len(s))

    widths = [min(w, 40) for w in widths]

    def make_row(cells):
        return "| " + " | ".join(str(c)[:40].ljust(w) for c, w in zip(cells, widths)) + " |"

    def make_sep():
        return "+" + "+".join("-" * (w + 2) for w in widths) + "+"

    print(f"  {make_sep()}")
    print(f"  {make_row(headers)}")
    print(f"  {make_sep()}")
    for row in rows[:20]:
        print(f"  {make_row(row)}")
    if len(rows) > 20:
        print(f"  ... 共 {len(rows)} 行，显示前 20 行")
    print(f"  {make_sep()}")


# ============================================================================
# 记忆相关打印工具
# ============================================================================

def print_user_memory_summary(user_memory: UserMemory):
    """打印用户记忆摘要"""
    print(f"\n{'=' * 60}")
    print(f"  用户记忆摘要 (user_id={user_memory.user_id})")
    print(f"{'=' * 60}")

    tables = user_memory.get_frequently_used_tables(top_k=10)
    print(f"  常用表 (top 10): {tables if tables else '(空)'}")

    metrics = user_memory.get_metric_definitions(min_confidence=0.0)
    if metrics:
        print(f"  指标定义 ({len(metrics)} 个):")
        for m in metrics:
            print(f"    - {m.get('name', '')} [conf={m.get('confidence', 0):.2f}, src={m.get('source', '')}]")
            print(f"      desc: {m.get('description', '')}")
            print(f"      sql:  {m.get('sql_pattern', '')[:80]}")
    else:
        print(f"  指标定义: (空)")

    prefs = user_memory.get_query_preferences()
    print(f"  查询偏好: {prefs if prefs else '(空)'}")

    domain = user_memory.get_domain_context()
    print(f"  领域上下文: {domain if domain else '(空)'}")
    print()


def print_session_history(session):
    """打印当前会话历史"""
    print(f"\n{'=' * 60}")
    print(f"  会话历史 (session_id={session.session_id[:8]}...)")
    print(f"{'=' * 60}")
    turn_count = session.get_turn_count()
    if turn_count == 0:
        print("  (空)")
        print()
        return

    turns = session.get_recent_turns(n=turn_count)
    for i, turn in enumerate(turns, 1):
        q = turn.get("user_query", "")
        sql = turn.get("final_sql", "")
        hit = turn.get("cache_hit", False)
        print(f"  轮次 {i}: {q}")
        print(f"    SQL:        {sql[:80] if sql else '(无)'}")
        print(f"    cache_hit:  {hit}")
        if turn.get("rejection_reason"):
            print(f"    rejected:   {turn['rejection_reason']}")
    print()


def print_trace(state: dict):
    """打印主图执行轨迹"""
    print(f"\n  执行轨迹:")
    for entry in state.get("trace_log", []):
        print(f"    -> {entry}")


def print_cache_status(state: dict):
    """打印命中检测结果"""
    if state.get("cache_hit"):
        print(f"\n  [缓存命中] source={state.get('cache_source')} "
              f"confidence={state.get('cache_confidence', 0):.2f}")
        print(f"  cached_sql: {state.get('cached_sql', '')[:100]}")
    else:
        print(f"\n  [缓存未命中] confidence={state.get('cache_confidence', 0):.2f}")


# ============================================================================
# 主流程
# ============================================================================

def main():
    print("=" * 60)
    print("  NL2SQL E2E Demo (含会话记忆 + 用户记忆)")
    print("=" * 60)

    # 1. 选数据库
    databases = find_bird_databases()
    if not databases:
        print("\n未在 data/ 目录找到数据库，请检查数据集是否正确放置")
        return

    db_names = list(databases.keys())
    print(f"\n找到 {len(db_names)} 个数据库:")
    for i, name in enumerate(db_names, 1):
        print(f"  {i}. {name}")

    try:
        choice = input(f"\n选择数据库 (1-{len(db_names)}): ").strip()
        db_idx = int(choice) - 1
        if db_idx < 0 or db_idx >= len(db_names):
            print("无效选择")
            return
    except (ValueError, KeyboardInterrupt, EOFError):
        return

    selected_db_id = db_names[db_idx]
    selected_db_path = databases[selected_db_id]
    selected_db_dir = str(Path(selected_db_path).parent)

    # 2. 选用户和会话
    try:
        user_id = input("\n输入 user_id（回车默认 'demo_user'）: ").strip() or "demo_user"
    except (KeyboardInterrupt, EOFError):
        return

    # 3. 初始化组件
    connector = DatabaseConnector(selected_db_path, db_type="sqlite")
    tables = connector.get_tables()
    print(f"\n数据库: {selected_db_id}, 表数: {len(tables)}")

    lsh_indexer = prepare_lsh_indexer(selected_db_dir)

    bge_model_path = os.getenv("BGE_M3_MODEL_PATH", "BAAI/bge-m3")
    model_name = os.getenv("QWEN_MODEL", "qwen3.6-plus-2026-04-02")

    data_dir = str(Path(__file__).parent.parent / "data")
    memory_dir = os.getenv("MEMORY_DIR", "memory")
    memory_dir = str(Path(__file__).parent.parent / memory_dir)
    vectorizer, vector_store = prepare_schema_index(data_dir, bge_model_path)

    try:
        llm_client = LLMClient(model=model_name)
        logger.info(f"LLM 初始化成功: {model_name}")
    except Exception as e:
        logger.error(f"LLM 初始化失败: {e}")
        connector.disconnect()
        return

    # 4. 各 Agent
    retriever = InformationRetrieval(
        llm_client=llm_client,
        lsh_indexer=lsh_indexer,
        vector_store=vector_store,
    )
    if vectorizer is not None:
        retriever._vectorizer = vectorizer

    selector = SchemaSelector(llm_client=llm_client, db_connector=connector)
    generator = SQLGenerator(llm_client=llm_client, num_candidates=3)
    executor = SQLExecutor(db_connector=connector)
    fix_loop = SQLFixLoop(executor=executor, llm_client=llm_client, max_retries=2)
    answerability_checker = AnswerabilityChecker(llm_client=llm_client, strictness="loose")
    result_verifier = ResultVerifier(llm_client=llm_client, strictness="strict")
    decider = SelfConsistencyDecision(llm_client=llm_client, result_verifier=result_verifier)

    # 5. 记忆组件
    history_cache = HistoryCache(llm_client=llm_client, min_confidence=0.8)
    memory_updater = MemoryUpdater(llm_client=llm_client)

    session_manager = SessionManager(
        base_dir=str(Path(memory_dir) / "sessions"),
        max_cache_size=20,
    )
    user_memory = UserMemory(user_id=user_id, base_dir=str(Path(memory_dir) / "user_memory"))
    user_memory.load()

    # 6. 选择会话
    existing_sessions = session_manager.list_sessions(user_id)
    if existing_sessions:
        print(f"\n用户 '{user_id}' 已有 {len(existing_sessions)} 个会话:")
        for i, s in enumerate(existing_sessions[:10], 1):
            print(f"  {i}. session={s['session_id'][:8]}... "
                  f"updated={s['updated_at']} turns={s['turn_count']}")
        print(f"  0. 新建一个会话")
        try:
            sc = input(f"\n选择会话 (0-{min(len(existing_sessions), 10)}): ").strip()
            sc_idx = int(sc)
            if sc_idx == 0:
                session = session_manager.create_session(user_id=user_id)
                print(f"  新会话已创建: {session.session_id[:8]}...")
            else:
                sid = existing_sessions[sc_idx - 1]["session_id"]
                session = session_manager.get_session(sid, user_id)
                print(f"  已加载会话: {sid[:8]}... ({session.get_turn_count()} 轮)")
        except (ValueError, IndexError, KeyboardInterrupt, EOFError):
            session = session_manager.create_session(user_id=user_id)
            print(f"  新会话已创建: {session.session_id[:8]}...")
    else:
        session = session_manager.create_session(user_id=user_id)
        print(f"\n用户 '{user_id}' 首次访问，新建会话: {session.session_id[:8]}...")

    # 7. 构建主图（含 history_cache 和 memory_update）
    graph = build_main_graph(
        retriever=retriever,
        selector=selector,
        generator=generator,
        fix_loop=fix_loop,
        decider=decider,
        answerability_checker=answerability_checker,
        history_cache=history_cache,
        memory_updater=memory_updater,
    )

    print(f"\n{'=' * 60}")
    print(f"  准备就绪")
    print(f"  user_id={user_id}, session={session.session_id[:8]}..., db={selected_db_id}")
    print(f"  交互命令: /new /sessions /memory /history /clear /switch  quit/q")
    print(f"{'=' * 60}")

    # 8. 交互循环
    while True:
        try:
            prompt = f"\n[{user_id}@{session.session_id[:6]}|{selected_db_id}] > "
            query = input(prompt).strip()
        except (KeyboardInterrupt, EOFError):
            break

        if not query:
            continue

        cmd = query.lower()
        if cmd in ("quit", "exit", "q"):
            break

        # 处理命令
        if cmd == "/new":
            session = session_manager.create_session(user_id=user_id)
            print(f"  新会话已创建: {session.session_id[:8]}...")
            continue
        if cmd == "/sessions":
            sessions = session_manager.list_sessions(user_id)
            print(f"\n  共 {len(sessions)} 个会话:")
            for s in sessions[:20]:
                marker = " * " if s["session_id"] == session.session_id else "   "
                print(f"  {marker}{s['session_id'][:8]}... updated={s['updated_at']} turns={s['turn_count']}")
            continue
        if cmd == "/memory":
            print_user_memory_summary(user_memory)
            continue
        if cmd == "/history":
            print_session_history(session)
            continue
        if cmd == "/clear":
            sid = session.session_id
            session_manager.delete_session(sid, user_id)
            session = session_manager.create_session(user_id=user_id)
            print(f"  已删除旧会话 {sid[:8]}..., 新建 {session.session_id[:8]}...")
            continue
        if cmd == "/switch":
            connector.disconnect()
            main()
            return

        # 走主图
        try:
            start = time.time()

            # 构建 initial_state，注入会话历史 + 用户记忆
            initial_state = create_initial_state(
                user_query=query,
                user_id=user_id,
                database_filter=selected_db_id,
            )
            recent_turns = session.get_recent_turns(n=5)
            initial_state["conversation_history"] = [t for t in recent_turns]
            initial_state["metric_definitions"] = user_memory.get_metric_definitions(min_confidence=0.7)
            initial_state["_user_memory"] = user_memory
            initial_state["_session_memory"] = session

            print(f"\n  注入: {len(recent_turns)} 轮历史, "
                  f"{len(initial_state['metric_definitions'])} 个指标定义")

            # 调用主图（同步 invoke）
            final_state = graph.invoke(initial_state)

            elapsed = time.time() - start

            # 打印命中状态
            print_cache_status(final_state)

            # 打印轨迹
            print_trace(final_state)

            # 错误/拒答
            err = final_state.get("error")
            rejection = final_state.get("rejection_reason")
            if rejection:
                print(f"\n{'=' * 60}")
                print(f"  拒答: {rejection}")
                print(f"{'=' * 60}")
                # 写入会话轮次
                session.add_turn({
                    "user_query": query,
                    "final_sql": "",
                    "final_result": None,
                    "cache_hit": final_state.get("cache_hit", False),
                    "rejection_reason": rejection,
                })
                continue
            if err and not final_state.get("final_sql"):
                print(f"\n{'=' * 60}")
                print(f"  执行错误: {err}")
                print(f"{'=' * 60}")
                session.add_turn({
                    "user_query": query,
                    "final_sql": "",
                    "final_result": None,
                    "cache_hit": final_state.get("cache_hit", False),
                    "error": err,
                })
                continue

            # 正常结果
            final_sql = final_state.get("final_sql", "")
            final_result = final_state.get("final_result")
            decision = final_state.get("final_decision")

            print(f"\n{'=' * 60}")
            print(f"  最终结果")
            print(f"{'=' * 60}")
            print(f"  SQL:        {final_sql}")
            if decision is not None:
                print(f"  决策理由:   {decision.decision_reason}")
            print(f"  总耗时:     {elapsed:.2f}s")

            if final_result:
                print(f"\n  查询结果:")
                print_result_table(final_result)

            # 写入会话轮次（memory_update 节点已经更新 UserMemory + SessionMemory 的 context_summary，
            # 这里再补一条完整的 turn）
            # 注意：不存 final_result，只存元信息（行数 + 列名），避免 Row 类型序列化失败 + 存储膨胀
            result_meta = None
            if isinstance(final_result, (list, tuple)) and final_result:
                first = final_result[0]
                columns = list(first.keys()) if hasattr(first, "keys") else []
                result_meta = {
                    "row_count": len(final_result),
                    "columns": columns,
                }

            session.add_turn({
                "user_query": query,
                "final_sql": final_sql,
                "result_meta": result_meta,
                "cache_hit": final_state.get("cache_hit", False),
            })

            print()

        except Exception as e:
            print(f"\n执行异常: {e}")
            import traceback
            traceback.print_exc()

    connector.disconnect()
    print("\nBye! 会话和记忆已持久化。下次启动可继续使用。")


if __name__ == "__main__":
    main()

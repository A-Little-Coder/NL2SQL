# ============================================================================
# NL2SQL 端到端真实试用脚本（使用 LangGraph 主图 + 真实向量索引）
# ============================================================================
# 调用真实 Qwen API，使用 BIRD-SQL 数据集中的 SQLite 数据库
#
# 使用前请先运行索引构建:
#   python scripts/build_lsh_index.py --db_id california_schools
#   python scripts/build_schema_index.py --db_id california_schools
#
# 用法:
#   python tests/test_e2e_live.py
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
from src.retrieval.information_retrieval import (
    InformationRetrieval, RetrievedContext, RetrievedItem
)
from src.schema_selection.schema_selector import SchemaSelector
from src.sql_generation.sql_generator import SQLGenerator, SQLStatus
from src.execution.executor import SQLExecutor, SQLFixLoop
from src.decision.self_consistency import SelfConsistencyDecision
from src.verification.answerability import AnswerabilityChecker
from src.verification.result_verifier import ResultVerifier
from src.graph import build_main_graph, create_initial_state
from utils.llm_client import LLMClient


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
        # 若没找到 {name}.sqlite，找任意 .sqlite
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
        logger.error("=" * 60)
        logger.error("未找到 LSH 索引！")
        logger.error("=" * 60)
        logger.error(f"请先运行索引构建:")
        logger.error(f"  python scripts/build_lsh_index.py --db_id {Path(db_directory).name}")
        logger.error("=" * 60)
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


def prepare_schema_index(db_id: str, data_dir: str, bge_model_path: str) -> tuple:
    """
    准备 schema 索引（全局单 collection）

    Returns:
        (vectorizer, vector_store) 或 (None, None)
    """
    persist_dir = Path(data_dir) / "preprocessed" / "chroma"
    collection_name = "nl2sql_columns"

    if not persist_dir.exists() or not (persist_dir / "chroma.sqlite3").exists():
        logger.error("=" * 60)
        logger.error("未找到 schema 向量索引！")
        logger.error("=" * 60)
        logger.error(f"请先运行索引构建:")
        logger.error(f"  python scripts/build_schema_index.py")
        logger.error("=" * 60)
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
        import traceback
        logger.debug(traceback.format_exc())
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


def main():
    print("=" * 60)
    print("  NL2SQL Agent - E2E Demo (真实索引 + LangGraph)")
    print("=" * 60)

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

    # 连接器
    connector = DatabaseConnector(selected_db_path, db_type="sqlite")
    tables = connector.get_tables()
    print(f"\n数据库: {selected_db_id}")
    print(f"表数: {len(tables)}")

    # LSH 索引
    lsh_indexer = prepare_lsh_indexer(selected_db_dir)

    # 环境变量配置
    bge_model_path = os.getenv("BGE_M3_MODEL_PATH", "BAAI/bge-m3")
    model_name = os.getenv("QWEN_MODEL", "qwen3.6-plus-2026-04-02")

    # 加载 BGE-M3（优先本地）
    data_dir = str(Path(__file__).parent.parent / "data")
    vectorizer, vector_store = prepare_schema_index(selected_db_id, data_dir, bge_model_path)

    # LLM 客户端
    llm_client = None
    try:
        llm_client = LLMClient(model=model_name)
        logger.info(f"LLM 初始化成功: {model_name}")
    except Exception as e:
        logger.error(f"LLM 初始化失败: {e}")
        logger.error("请检查 .env 中的 QWEN_API_KEY 是否配置")
        connector.disconnect()
        return

    # 初始化各个 Agent（用于 build_main_graph）
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
    decider = SelfConsistencyDecision(llm_client=llm_client)

    # 可回答性检查器（决策 23）和结果验证器（决策 24）
    answerability_checker = AnswerabilityChecker(llm_client=llm_client, strictness="loose")
    result_verifier = ResultVerifier(llm_client=llm_client, strictness="strict")
    decider = SelfConsistencyDecision(llm_client=llm_client, result_verifier=result_verifier)

    # 构建主图
    graph = build_main_graph(
        retriever=retriever,
        selector=selector,
        generator=generator,
        fix_loop=fix_loop,
        decider=decider,
        answerability_checker=answerability_checker,
    )

    print(f"\n{'=' * 60}")
    print(f"  准备就绪！对 {selected_db_id} 提问")
    print(f"  输入 'switch' 换库，输入 'quit' 退出")
    print(f"{'=' * 60}")

    while True:
        try:
            query = input(f"\n[{selected_db_id}] > ").strip()
        except (KeyboardInterrupt, EOFError):
            break

        if not query:
            continue
        if query.lower() in ("quit", "exit", "q"):
            break
        if query.lower() == "switch":
            connector.disconnect()
            main()
            return

        try:
            start = time.time()
            initial_state = create_initial_state(
                user_query=query,
                user_id="demo_user",
                database_filter=selected_db_id,
            )

            # 走主图（当前 §18 先不做 Clarification 子图，直接沿用组件实现）
            # 由于 Clarification 子图占位，我们先在外部走一遍原流程
            # 未来接入完整子图后用 graph.invoke()

            # ==========================================
            # 临时：手动跑完整流程（不经过 LangGraph 主图循环）
            # ==========================================

            # 1. IR
            print("\n[1/6] 信息检索 (IR)")
            context = retriever.retrieve(query, database_filter=selected_db_id)
            print(f"  关键词: {context.keywords}")
            if context.tables:
                print(f"  表: {[t.name for t in context.tables]}")
            if context.columns:
                print(f"  列: {[c.name for c in context.columns[:10]]}" + (f" + {len(context.columns)-10} 更多" if len(context.columns) > 10 else ""))
            if context.values:
                print(f"  值命中: {[v.name for v in context.values[:10]]}" + (f" + {len(context.values)-10} 更多" if len(context.values) > 10 else ""))

            # 2. SS
            print("\n[2/6] Schema 选择 (SS)")
            selected_schema = selector.select(context, query)
            for tbl in selected_schema:
                cols = [f"{c.name}" for c in tbl.columns]
                print(f"  {tbl.name}: {cols}")

            # 2.5 可回答性检查（决策 23）
            print("\n[3/6] 可回答性检查")
            answerability = answerability_checker.check(
                user_query=query,
                mschema=selected_schema,
                ir_context=context,
            )
            print(f"  结果: {answerability.answerable} (置信度: {answerability.confidence:.2f})")
            if answerability.answerable != "false":
                print(f"  理由: {answerability.reason}")
            else:
                print(f"  拒答原因: {answerability.reason}")
                print(f"  缺少信息: {answerability.missing_info}")
                print(f"\n{'=' * 60}")
                print(f"  拒答: 数据库无法回答此问题")
                print(f"{'=' * 60}")
                print(f"  原因: {answerability.reason}")
                print()
                continue

            # 3. CG
            print("\n[4/6] SQL 生成 (CG)")
            candidates = generator.generate(selected_schema, query)
            if not candidates:
                print("  未生成有效 SQL")
                continue
            for i, cand in enumerate(candidates, 1):
                print(f"  [候选{i}] {cand.sql}")

            # 4. Execution
            print("\n[5/6] 执行 (Execution)")
            from src.schema_selection.schema_selector import MSchemaFormat
            schema_text = MSchemaFormat.format_for_llm(MSchemaFormat.create_mschema_schema(selected_schema))
            for cand in candidates:
                result = fix_loop.run(cand.sql, query, schema_text)
                cand.result = result.result_data
                cand.execution_time = result.execution_time
                cand.status = SQLStatus.SUCCESS if result.success else SQLStatus.FAILED
                cand.error_message = result.error.original_message if result.error else None
                icon = "[OK]" if result.success else "[ERR]"
                print(f"  {icon} {cand.sql}")

            # 5. Decision
            print("\n[6/6] 自洽决策 (Decision)")
            decision = decider.decide(candidates, query, mschema=selected_schema)

            elapsed = time.time() - start

            # 检查结果验证拒答
            voting = decision.voting_summary or {}
            if voting.get("rejected"):
                print(f"\n{'=' * 60}")
                print(f"  拒答: 结果不可信")
                print(f"{'=' * 60}")
                print(f"  原因: {decision.decision_reason}")
                print()
                continue

            print(f"\n{'=' * 60}")
            print(f"  最终结果")
            print(f"{'=' * 60}")
            print(f"  SQL: {decision.selected_sql}")
            print(f"  决策理由: {decision.decision_reason}")
            print(f"  总耗时: {elapsed:.2f}s")

            if decision.execution_time:
                print(f"  SQL 执行: {decision.execution_time:.3f}s")

            if decision.selected_result:
                print(f"\n  查询结果:")
                print_result_table(decision.selected_result)
            print()

        except Exception as e:
            print(f"\n执行异常: {e}")
            import traceback
            traceback.print_exc()

    connector.disconnect()
    print("\nBye!")


if __name__ == "__main__":
    main()

# ============================================================================
# Schema 离线索引构建工具（全局单 Collection）
# ============================================================================
# 所有数据库的列向量存放在同一个 ChromaDB collection（nl2sql_columns）中，
# 通过 metadata.database 字段区分归属。
#
# 函数调用方式:
#   from tests.preprocessing.build_schema_index import build_all_schema_index, build_schema_index_for_db
#   build_schema_index_for_db("california_schools")  # 追加单个库
#   build_all_schema_index()  # 全量构建（自动去重）
#
# 直接运行:
#   python tests/preprocessing/build_schema_index.py  # 默认全量
# ============================================================================


import os
import sys
from pathlib import Path

# Windows + PyTorch + numpy 环境下，OpenMP 库可能重复加载导致段错误（exit code -1073741819）
# 必须在 import torch / FlagEmbedding 之前设置
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
os.environ["OMP_NUM_THREADS"] = "4"
os.environ["MKL_NUM_THREADS"] = "4"

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
load_dotenv()

from loguru import logger

from src.preprocessing.database_connector import DatabaseConnector
from src.preprocessing.schema_vectorizer import SchemaVectorizer
from src.preprocessing.schema_doc_generator import SchemaColumnDocGenerator
from src.preprocessing.vector_store import VectorStoreManager


# 全局常量
COLLECTION_NAME = "nl2sql_columns"


def get_persist_dir(data_dir: str = None) -> str:
    """获取全局 ChromaDB 持久化目录"""
    if data_dir is None:
        data_dir = str(Path(__file__).parent.parent.parent / "data")
    return str(Path(data_dir) / "preprocessed" / "chroma")


def find_bird_databases(data_dir: str = None) -> dict:
    """扫描 data/ 下所有 BIRD-SQL 数据库，返回 {db_id: db_file_path}"""
    if data_dir is None:
        data_dir = str(Path(__file__).parent.parent.parent / "data")
    databases = {}
    data_path = Path(data_dir)
    if not data_path.exists():
        return databases
    for entry in sorted(data_path.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        db_path = None
        for ext in [".sqlite", ".db"]:
            f = entry / f"{entry.name}{ext}"
            if f.exists():
                db_path = str(f)
                break
        if db_path is None:
            for f in entry.glob("*.sqlite"):
                db_path = str(f)
                break
        if db_path is not None:
            databases[entry.name] = str(db_path)
    return databases


def _collect_columns_for_db(db_id: str, db_path: str) -> list:
    """
    连接数据库，读取所有表列，生成文档+metadata

    Returns:
        List[dict]: [{"id": str, "document": str, "metadata": dict}, ...]
    """
    connector = DatabaseConnector(db_path, db_type="sqlite")
    tables = connector.get_tables()
    if not tables:
        logger.warning(f"{db_id} 没有表")
        return []

    items = []
    for table_name in tables:
        schema = connector.get_table_schema(table_name, sample_size=5, include_description=True)
        foreign_keys = {fk["column"]: fk for fk in schema.get("foreign_keys", [])}

        for col in schema["columns"]:
            col_name = col["name"]
            col_schema = dict(col)
            if col_name in foreign_keys:
                fk = foreign_keys[col_name]
                col_schema["is_foreign_key"] = True
                col_schema["references_table"] = fk["references_table"]
                col_schema["references_column"] = fk["references_column"]
            else:
                col_schema["is_foreign_key"] = False
            col_schema["sample_values"] = schema.get("sample_values", {}).get(col_name, [])

            doc_meta = SchemaColumnDocGenerator.build_doc_from_connector_schema(
                database=db_id,
                table_name=table_name,
                col_schema=col_schema,
            )
            items.append({
                "id": f"{db_id}.{table_name}.{col_name}",
                "document": doc_meta["document"],
                "metadata": doc_meta["metadata"],
            })

    return items


def build_schema_index_for_db(db_id: str, data_dir: str = None,
                              bge_model_path: str = None,
                              force_rebuild: bool = False):
    """
    为单个数据库构建（追加到全局 collection）

    如果全局 collection 尚不存在则会自动创建；
    force_rebuild 会清空整个 collection（所有库的数据都丢失，慎用）。
    """
    if data_dir is None:
        data_dir = str(Path(__file__).parent.parent.parent / "data")

    if bge_model_path is None:
        bge_model_path = os.getenv("BGE_M3_MODEL_PATH", "BAAI/bge-m3")

    db_map = find_bird_databases(data_dir)
    if db_id not in db_map:
        logger.error(f"未找到数据库: {db_id}")
        return False

    db_path = db_map[db_id]
    persist_dir = get_persist_dir(data_dir)

    # 初始化向量化器（只加载一次）
    vectorizer = SchemaVectorizer(model_name=bge_model_path, device="cpu")
    try:
        vectorizer.load_model()
    except Exception as e:
        logger.error(f"加载 BGE-M3 失败: {e}")
        logger.error("请检查 BGE_M3_MODEL_PATH 环境变量")
        return False

    # 初始化/连接全局向量存储
    vector_store = VectorStoreManager(
        collection_name=COLLECTION_NAME,
        persist_directory=persist_dir,
    )

    if force_rebuild:
        logger.warning(f"强制重建: 清空全局 collection {COLLECTION_NAME}")
        vector_store.clear()

    # 收集列数据
    items = _collect_columns_for_db(db_id, db_path)
    if not items:
        return True

    logger.info(f"{db_id}: 收集到 {len(items)} 个列")

    # 批量向量化
    documents = [i["document"] for i in items]
    logger.info(f"向量化 {len(documents)} 个文档")
    embeddings = vectorizer.embed_texts(documents, return_dense=True)
    dense_vectors = embeddings.get("dense", [])

    # 组装 upsert 数据
    chroma_items = []
    for i, item in enumerate(items):
        chroma_items.append({
            "id": item["id"],
            "embedding": dense_vectors[i] if i < len(dense_vectors) else None,
            "metadata": item["metadata"],
            "document": item["document"],
        })

    ok = vector_store.add_embeddings(chroma_items)
    if ok:
        logger.success(f"{db_id} 追加完成: {len(chroma_items)} 个列")
        # 构建完成后自动写入 Manifest
        try:
            from src.preprocessing.manifest import write_manifest_for_db
            write_manifest_for_db(db_id, data_dir, module="schema_index")
        except Exception as e:
            logger.warning(f"写入 Manifest 失败 ({db_id}): {e}")
    return ok


def build_all_schema_index(data_dir: str = None, bge_model_path: str = None,
                           force_rebuild: bool = False):
    """
    全量构建所有数据库的 Schema 索引

    流程：
      1. 连接所有数据库，收集全部列文档
      2. 一次性批量向量化
      3. 一次性 upsert 到全局 collection

    这样比逐库 embed + upsert 更高效（减少模型加载次数）。
    """
    if data_dir is None:
        data_dir = str(Path(__file__).parent.parent.parent / "data")

    if bge_model_path is None:
        bge_model_path = os.getenv("BGE_M3_MODEL_PATH", "BAAI/bge-m3")

    db_map = find_bird_databases(data_dir)
    logger.info(f"找到 {len(db_map)} 个数据库")

    if not db_map:
        logger.warning("没有找到任何数据库，跳过")
        return 0, 0

    # 1. 收集所有数据库的所有列
    all_items = []  # [{"id", "document", "metadata", "db_id"}, ...]
    for db_id in sorted(db_map.keys()):
        try:
            items = _collect_columns_for_db(db_id, db_map[db_id])
            for item in items:
                item["db_id"] = db_id
            all_items.extend(items)
            logger.info(f"{db_id}: {len(items)} 个列")
        except Exception as e:
            logger.error(f"{db_id} 列收集失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())

    if not all_items:
        logger.warning("没有收集到任何列数据")
        return 0, 0

    logger.info(f"共收集到 {len(all_items)} 个列，来自 {len(db_map)} 个数据库")

    # 2. 初始化向量化器
    vectorizer = SchemaVectorizer(model_name=bge_model_path, device="cpu")
    vectorizer.load_model()

    # 3. 分块批量向量化（避免 CPU 一次性占用过多内存导致段错误）
    documents = [i["document"] for i in all_items]
    logger.info(f"开始批量向量化 {len(documents)} 个文档")

    BATCH_SIZE = 32  # CPU 友好的小批量
    dense_vectors = []
    for batch_start in range(0, len(documents), BATCH_SIZE):
        batch_end = min(batch_start + BATCH_SIZE, len(documents))
        batch_docs = documents[batch_start:batch_end]
        logger.info(f"  向量化 batch [{batch_start}:{batch_end}] / {len(documents)}")
        batch_result = vectorizer.embed_texts(batch_docs, return_dense=True, batch_size=BATCH_SIZE)
        dense_vectors.extend(batch_result.get("dense", []))
    logger.info(f"向量化完成，共 {len(dense_vectors)} 条")

    # 4. 初始化/连接全局向量存储
    persist_dir = get_persist_dir(data_dir)
    vector_store = VectorStoreManager(
        collection_name=COLLECTION_NAME,
        persist_directory=persist_dir,
    )

    if force_rebuild:
        logger.warning(f"强制重建: 清空全局 collection")
        vector_store.clear()

    # 5. 批量 upsert
    chroma_items = []
    for i, item in enumerate(all_items):
        chroma_items.append({
            "id": item["id"],
            "embedding": dense_vectors[i] if i < len(dense_vectors) else None,
            "metadata": item["metadata"],
            "document": item["document"],
        })

    ok = vector_store.add_embeddings(chroma_items)
    success_count = len(db_map) if ok else 0
    if ok:
        logger.success(f"全量 Schema 索引构建完成: {len(chroma_items)} 个列，来自 {len(db_map)} 个数据库")
    return success_count, len(db_map)


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("Schema 离线索引 - 全量构建（全局单 Collection）")
    logger.info("=" * 60)
    build_all_schema_index()

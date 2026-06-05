# ============================================================================
# 表关联图批量构建脚本
# ============================================================================
# 为 BIRD-SQL 数据集中的所有（或指定）数据库构建表关联图。
#
# 直接运行:
#   python src/preprocessing/build_schema_graphs.py
#   python src/preprocessing/build_schema_graphs.py --db_id california_schools
#   python src/preprocessing/build_schema_graphs.py --skip_llm
#
# 代码调用:
#   from src.preprocessing.build_schema_graphs import build_schema_graphs
#   build_schema_graphs(db_id="california_schools")
# ============================================================================


import os
import sys
from pathlib import Path

# Windows + PyTorch + numpy 环境下，OpenMP 库可能重复加载导致段错误
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
os.environ["OMP_NUM_THREADS"] = "4"
os.environ["MKL_NUM_THREADS"] = "4"

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
load_dotenv()

from loguru import logger

from src.preprocessing.database_connector import DatabaseConnector
from src.preprocessing.schema_graph_builder import SchemaGraphBuilder
from src.preprocessing.build_schema_index import (
    COLLECTION_NAME,
    find_bird_databases,
    get_persist_dir,
)
from src.preprocessing.vector_store import VectorStoreManager


def _get_output_dir(data_dir: str) -> str:
    return str(Path(data_dir) / "preprocessed" / "schema_graphs")


def build_schema_graphs(
    db_id: str = None,
    data_dir: str = None,
    skip_llm: bool = False,
    hit_rate_threshold: float = 0.5,
    top_similar_pairs: int = 3,
    sample_size: int = 20,
    llm_client=None,
) -> int:
    """
    构建表关联图

    Args:
        db_id: 指定数据库 ID，None 表示全量构建
        data_dir: 数据目录（默认项目根目录下的 data/）
        skip_llm: 是否跳过 Stage 3（LLM 辅助）
        hit_rate_threshold: 值命中率阈值（默认 0.5）
        top_similar_pairs: 每对表取向量最相似的 top N 列对
        sample_size: 命中率检测采样数量
        llm_client: 外部传入的 LLM 客户端（不传则自动尝试加载）

    Returns:
        int: 成功构建的数据库数量
    """
    if data_dir is None:
        data_dir = str(Path(__file__).parent.parent.parent / "data")

    # 扫描数据库
    db_map = find_bird_databases(data_dir)
    if not db_map:
        logger.error("未找到任何数据库")
        return 0

    # 过滤
    if db_id:
        if db_id not in db_map:
            logger.error(f"未找到数据库: {db_id}")
            return 0
        db_map = {db_id: db_map[db_id]}

    logger.info(f"准备构建 {len(db_map)} 个数据库的表关联图")

    # 连接向量库
    persist_dir = get_persist_dir(data_dir)
    vector_store = VectorStoreManager(
        collection_name=COLLECTION_NAME,
        persist_directory=persist_dir,
    )

    # LLM 客户端
    if not skip_llm and llm_client is None:
        try:
            from utils.llm_client import LLMClient
            llm_client = LLMClient()
            logger.info("LLM 客户端已加载")
        except Exception as e:
            logger.warning(f"LLM 客户端加载失败，将跳过 Stage 3: {e}")
            skip_llm = True

    # 逐库构建
    output_dir = _get_output_dir(data_dir)
    success_count = 0

    for db_id, db_path in sorted(db_map.items()):
        logger.info(f"{'=' * 40}")
        logger.info(f"构建: {db_id}")
        try:
            connector = DatabaseConnector(db_path, db_type="sqlite")

            builder = SchemaGraphBuilder(
                db_connector=connector,
                vector_store=vector_store,
                llm_client=llm_client if not skip_llm else None,
                hit_rate_threshold=hit_rate_threshold,
                top_similar_pairs=top_similar_pairs,
                sample_size=sample_size,
            )

            graph = builder.build(db_id=db_id)

            output_path = os.path.join(output_dir, f"{db_id}.json")
            SchemaGraphBuilder.save(graph, output_path)

            # 摘要
            graph_data = list(graph.values())[0]
            edges = graph_data.get("edges", [])
            type_counts = {}
            for e in edges:
                t = e.get("type", "unknown")
                type_counts[t] = type_counts.get(t, 0) + 1
            logger.info(f"{db_id}: {len(edges)} 条边 ({type_counts})")

            connector.disconnect()
            success_count += 1

            # 构建完成后自动写入 / 更新 Manifest
            try:
                from src.preprocessing.manifest import write_manifest_for_db
                write_manifest_for_db(db_id, data_dir, module="schema_graph")
            except Exception as e:
                logger.warning(f"写入 Manifest 失败 ({db_id}): {e}")

        except Exception as e:
            logger.error(f"{db_id} 构建失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())

    logger.info(f"{'=' * 40}")
    logger.info(f"构建完成: {success_count}/{len(db_map)} 个数据库成功")
    return success_count


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("表关联图 - 全量构建")
    logger.info("=" * 60)
    build_schema_graphs()

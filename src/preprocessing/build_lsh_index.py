# ============================================================================
# LSH 离线索引构建工具
# ============================================================================
# 提供函数调用 + 直接全量运行两种方式
#
# 函数调用方式:
#   from tests.preprocessing.build_lsh_index import build_all_lsh, build_lsh_for_db
#   build_lsh_for_db("california_schools")  # 单个库
#   build_all_lsh()  # 全量
#
# 直接运行:
#   python tests/preprocessing/build_lsh_index.py  # 默认全量
# ============================================================================


import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from loguru import logger

from src.preprocessing.lsh_index import LSHIndexer


def find_bird_databases(data_dir: str = None) -> dict:
    if data_dir is None:
        data_dir = str(Path(__file__).parent.parent.parent / "data")
    databases = {}
    data_path = Path(data_dir)
    if not data_path.exists():
        return databases
    for entry in sorted(data_path.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        # 找 {db_id}.sqlite 或任意 .sqlite
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
            databases[entry.name] = str(entry)
    return databases


def build_lsh_for_db(db_id: str, data_dir: str = None,
                     force_rebuild: bool = False,
                     signature_size: int = 128,
                     n_gram: int = 3,
                     lsh_threshold: float = 0.5):
    if data_dir is None:
        data_dir = str(Path(__file__).parent.parent.parent / "data")

    db_map = find_bird_databases(data_dir)
    if db_id not in db_map:
        logger.error(f"未找到数据库: {db_id}")
        return False

    db_directory = db_map[db_id]
    indexer = LSHIndexer(signature_size=signature_size, n_gram=n_gram, threshold=lsh_threshold)

    if LSHIndexer.is_lsh_built(db_directory):
        if force_rebuild:
            logger.warning(f"强制重建: 删除 {db_id} 的 LSH 索引")
            preprocessed_dir = Path(db_directory) / "preprocessed"
            lsh_pickle = preprocessed_dir / "lsh" / "lsh_index.pkl"
            minhash_pickle = preprocessed_dir / "lsh" / "minhashes.pkl"
            if lsh_pickle.exists():
                lsh_pickle.unlink()
            if minhash_pickle.exists():
                minhash_pickle.unlink()
        else:
            logger.info(f"{db_id} 的 LSH 索引已存在，跳过")
            return True

    logger.info(f"开始构建 {db_id} 的 LSH 索引")
    try:
        indexer.build_db_lsh(db_directory, verbose=True)
        logger.success(f"{db_id} LSH 索引构建完成")
        return True
    except Exception as e:
        logger.error(f"{db_id} LSH 索引构建失败: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        return False


def build_all_lsh(data_dir: str = None, force_rebuild: bool = False,
                  signature_size: int = 128, n_gram: int = 3, lsh_threshold: float = 0.5):
    db_map = find_bird_databases(data_dir)
    logger.info(f"找到 {len(db_map)} 个数据库，开始全量 LSH 索引构建")
    success_count = 0
    for db_id in sorted(db_map.keys()):
        ok = build_lsh_for_db(db_id, data_dir=data_dir, force_rebuild=force_rebuild,
                              signature_size=signature_size, n_gram=n_gram, lsh_threshold=lsh_threshold)
        if ok:
            success_count += 1
    logger.info(f"全量 LSH 索引构建完成: {success_count}/{len(db_map)} 成功")
    return success_count, len(db_map)


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("LSH 离线索引 - 全量构建")
    logger.info("=" * 60)
    build_all_lsh()

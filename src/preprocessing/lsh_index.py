# ============================================================================
# LSH (局部敏感哈希) 索引生成器
# ============================================================================
# 功能说明:
#   为数据库中各字段的唯一值创建 LSH 索引，用于快速近似值匹配检索
#   基于 datasketch 库的 MinHashLSH 实现，参考 CHESS 项目的 LSH 方案
#
# 使用方法:
#   # 预处理：为整个数据库构建 LSH 索引
#   LSHIndexer.build_db_lsh("data/formula_1/")
#
#   # 查询：加载索引并搜索相似值
#   lsh, minhashes = LSHIndexer.load_db_lsh("data/formula_1/")
#   results = LSHIndexer.query_lsh(lsh, minhashes, "Hamilton")
#
# ============================================================================


import os
import pickle
import sqlite3
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional
from tqdm import tqdm

from datasketch import MinHash, MinHashLSH


class LSHIndexer:
    """
    LSH 索引生成器和检索器

    用于对字段值进行快速近似匹配，特别适合处理：
    - 拼写变体（"Apple" vs "apple"）
    - 部分匹配（"Hamilton" vs "Lewis Hamilton"）
    - 格式差异（"2023-01" vs "Jan 2023"）

    基于 datasketch 的 MinHashLSH 实现，核心流程：
    1. 从数据库提取 TEXT 列的唯一值
    2. 对每个值计算 MinHash 签名（n-gram 方式）
    3. 将签名插入 MinHashLSH 索引
    4. 查询时计算查询值的 MinHash，在 LSH 中找相似候选

    Attributes:
        signature_size (int): MinHash 签名长度，默认 128
        n_gram (int): n-gram 大小，默认 3
        threshold (float): LSH 相似度阈值，默认 0.5
    """

    # 构建索引时跳过的列名关键词（参考 CHESS）
    SKIP_KEYWORDS = ["_id", " id", "url", "email", "web", "time", "phone", "date", "address"]

    def __init__(self, signature_size: int = 128, n_gram: int = 3, threshold: float = 0.5):
        """
        初始化 LSH 索引器

        Args:
            signature_size: MinHash 签名长度（num_perm）
                          - 128: 平衡精度和性能（推荐）
                          - 256: 更高精度，更多内存
            n_gram: n-gram 大小，用于将字符串转为集合
                   - 3: 适合英文（默认）
                   - 2: 适合短字符串或中文
            threshold: LSH 相似度阈值（Jaccard）
                      - 0.5: 平衡召回率和精确率（推荐）
                      - 0.3: 更高召回率，更多误报
                      - 0.7: 更高精确率，可能漏检
        """
        self.signature_size = signature_size
        self.n_gram = n_gram
        self.threshold = threshold

    # ========================================================================
    # MinHash 相关
    # ========================================================================

    @staticmethod
    def create_minhash(value: str, signature_size: int = 128, n_gram: int = 3) -> MinHash:
        """
        为单个值创建 MinHash 签名

        使用 n-gram 方式将字符串转换为集合，再计算 MinHash。

        Args:
            value: 输入字符串
            signature_size: 签名长度
            n_gram: n-gram 大小

        Returns:
            MinHash: 计算好的 MinHash 对象

        示例:
        ```python
        mh = LSHIndexer.create_minhash("Hamilton", signature_size=128, n_gram=3)
        # n-grams: ["Ham", "ami", "mit", "ilt", "lto", "ton"]
        ```
        """
        m = MinHash(num_perm=signature_size)
        for d in [value[i:i + n_gram] for i in range(max(len(value) - n_gram + 1, 1))]:
            m.update(d.encode('utf8'))
        return m

    @staticmethod
    def jaccard_similarity(mh1: MinHash, mh2: MinHash) -> float:
        """
        计算两个 MinHash 之间的 Jaccard 相似度估计值

        Args:
            mh1: 第一个 MinHash
            mh2: 第二个 MinHash

        Returns:
            float: 相似度（0-1 之间），1.0 表示完全相同
        """
        return mh1.jaccard(mh2)

    # ========================================================================
    # 从数据库提取唯一值
    # ========================================================================

    @staticmethod
    def get_unique_values(db_path: str) -> Dict[str, Dict[str, List[str]]]:
        """
        从 SQLite 数据库中提取 TEXT 列的唯一值

        参考 CHESS 的 _get_unique_values 实现，会自动跳过：
        - 主键列
        - ID/URL/Email/时间等低价值列
        - 值过长或过多的高基数列

        Args:
            db_path: SQLite 数据库文件路径

        Returns:
            Dict[str, Dict[str, List[str]]]: 唯一值字典
                {
                    "circuits": {
                        "name": ["Silverstone", "Monaco", ...],
                        "location": ["Northamptonshire", ...],
                        "country": ["UK", "Monaco", ...]
                    },
                    "drivers": {
                        "forename": ["Lewis", "Max", ...],
                        ...
                    }
                }
        """
        unique_values: Dict[str, Dict[str, List[str]]] = {}

        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()

            # 获取所有表名
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence'")
            table_names = [row[0] for row in cursor.fetchall()]

            # 收集主键列名
            primary_keys = []
            for table_name in table_names:
                cursor.execute(f"PRAGMA table_info('{table_name}')")
                for col in cursor.fetchall():
                    if col[5] > 0:  # pk 标志
                        if col[1].lower() not in [c.lower() for c in primary_keys]:
                            primary_keys.append(col[1])

            # 逐表逐列提取值
            for table_name in table_names:
                cursor.execute(f"PRAGMA table_info('{table_name}')")
                columns = cursor.fetchall()

                table_values: Dict[str, List[str]] = {}

                for col in columns:
                    col_name = col[1]
                    col_type = col[2] or ""

                    # 只处理 TEXT 列，跳过主键
                    if "TEXT" not in col_type.upper():
                        continue
                    if col_name.lower() in [c.lower() for c in primary_keys]:
                        continue

                    # 跳过低价值列（ID、URL 等）
                    if any(kw in col_name.lower() for kw in LSHIndexer.SKIP_KEYWORDS):
                        continue
                    if col_name.endswith("Id"):
                        continue

                    # 检查列的值规模
                    try:
                        cursor.execute(f"""
                            SELECT SUM(LENGTH(unique_values)), COUNT(unique_values)
                            FROM (
                                SELECT DISTINCT `{col_name}` AS unique_values
                                FROM `{table_name}`
                                WHERE `{col_name}` IS NOT NULL
                            )
                        """)
                        result = cursor.fetchone()
                        sum_lengths, count_distinct = result

                        if sum_lengths is None or count_distinct == 0:
                            continue

                        avg_length = sum_lengths / count_distinct

                        # 筛选策略（参考 CHESS）
                        is_name_col = "name" in col_name.lower()
                        if is_name_col and sum_lengths < 5_000_000:
                            pass  # name 列放宽限制
                        elif sum_lengths > 2_000_000 or avg_length > 25:
                            continue
                        if count_distinct > 10000:
                            continue

                    except Exception:
                        continue

                    # 提取唯一值
                    try:
                        cursor.execute(
                            f"SELECT DISTINCT `{col_name}` FROM `{table_name}` "
                            f"WHERE `{col_name}` IS NOT NULL"
                        )
                        values = [str(row[0]) for row in cursor.fetchall()]
                        table_values[col_name] = values
                    except Exception:
                        continue

                if table_values:
                    unique_values[table_name] = table_values

            conn.close()

        except Exception as e:
            print(f"[错误] 提取唯一值失败：{e}")

        return unique_values

    # ========================================================================
    # 构建 LSH 索引
    # ========================================================================

    def build_index(self, unique_values: Dict[str, Dict[str, List[str]]],
                    verbose: bool = True) -> Tuple[MinHashLSH, Dict[str, Tuple[MinHash, str, str, str]]]:
        """
        从唯一值字典构建 MinHashLSH 索引

        Args:
            unique_values: 唯一值字典，格式同 get_unique_values() 返回
            verbose: 是否显示进度条

        Returns:
            Tuple[MinHashLSH, Dict]: (LSH 索引, minhashes 字典)
                minhashes 字典格式:
                {
                    "circuits_name_0": (MinHash, "circuits", "name", "Silverstone"),
                    "circuits_name_1": (MinHash, "circuits", "name", "Monaco"),
                    ...
                }

        示例:
        ```python
        indexer = LSHIndexer(signature_size=128, threshold=0.5)
        unique_values = indexer.get_unique_values("data/formula_1/formula_1.sqlite")
        lsh, minhashes = indexer.build_index(unique_values)
        ```
        """
        lsh = MinHashLSH(threshold=self.threshold, num_perm=self.signature_size)
        minhashes: Dict[str, Tuple[MinHash, str, str, str]] = {}

        total_values = sum(
            len(col_values)
            for table_values in unique_values.values()
            for col_values in table_values.values()
        )

        progress_bar = tqdm(total=total_values, desc="构建 LSH 索引") if verbose else None

        for table_name, table_values in unique_values.items():
            for column_name, column_values in table_values.items():
                for idx, value in enumerate(column_values):
                    try:
                        mh = self.create_minhash(value, self.signature_size, self.n_gram)
                        key = f"{table_name}_{column_name}_{idx}"
                        minhashes[key] = (mh, table_name, column_name, value)
                        lsh.insert(key, mh)
                    except Exception:
                        continue

                    if progress_bar:
                        progress_bar.update(1)

        if progress_bar:
            progress_bar.close()

        return lsh, minhashes

    # ========================================================================
    # 查询
    # ========================================================================

    def query(self, lsh: MinHashLSH, minhashes: Dict[str, Tuple[MinHash, str, str, str]],
              keyword: str, top_k: int = 10) -> Dict[str, Dict[str, List[str]]]:
        """
        在 LSH 索引中查询与关键词相似的值

        Args:
            lsh: MinHashLSH 索引
            minhashes: minhashes 字典
            keyword: 查询关键词，例如 "Hamilton"
            top_k: 返回前 k 个最相似的结果

        Returns:
            Dict[str, Dict[str, List[str]]]: 按表和列分组的结果
                {
                    "drivers": {
                        "forename": ["Lewis"],
                        "surname": ["Hamilton"]
                    }
                }

        示例:
        ```python
        indexer = LSHIndexer()
        results = indexer.query(lsh, minhashes, "Hamilton", top_k=10)
        for table, columns in results.items():
            for col, values in columns.items():
                print(f"  {table}.{col}: {values}")
        ```
        """
        query_mh = self.create_minhash(keyword, self.signature_size, self.n_gram)

        # 在 LSH 中查找候选
        results = lsh.query(query_mh)

        if not results:
            return {}

        # 计算精确相似度并排序
        similarities = []
        for result_key in results:
            if result_key in minhashes:
                mh, table_name, column_name, value = minhashes[result_key]
                sim = self.jaccard_similarity(query_mh, mh)
                similarities.append((result_key, table_name, column_name, value, sim))

        # 按相似度降序排序，取 top_k
        similarities.sort(key=lambda x: x[4], reverse=True)
        similarities = similarities[:top_k]

        # 按表和列分组
        grouped: Dict[str, Dict[str, List[str]]] = {}
        for _, table_name, column_name, value, sim in similarities:
            if table_name not in grouped:
                grouped[table_name] = {}
            if column_name not in grouped[table_name]:
                grouped[table_name][column_name] = []
            grouped[table_name][column_name].append(value)

        return grouped

    # ========================================================================
    # 持久化：构建整个数据库的 LSH 索引并保存
    # ========================================================================

    def build_db_lsh(self, db_directory_path: str, verbose: bool = True) -> None:
        """
        为整个数据库构建 LSH 索引并保存到文件

        生成的文件保存在 db_directory_path/preprocessed/ 目录下：
        - {db_id}_lsh.pkl: MinHashLSH 索引
        - {db_id}_minhashes.pkl: MinHash 签名字典
        - {db_id}_unique_values.pkl: 唯一值字典

        Args:
            db_directory_path: 数据库目录路径
                             例如 "data/formula_1/"
            verbose: 是否显示进度条

        示例:
        ```python
        indexer = LSHIndexer(signature_size=128, threshold=0.5)
        indexer.build_db_lsh("data/formula_1/")
        # 生成文件:
        #   data/formula_1/preprocessed/formula_1_lsh.pkl
        #   data/formula_1/preprocessed/formula_1_minhashes.pkl
        #   data/formula_1/preprocessed/formula_1_unique_values.pkl
        ```
        """
        db_dir = Path(db_directory_path)
        db_id = db_dir.name

        # 查找 sqlite 文件
        db_file = db_dir / f"{db_id}.sqlite"
        if not db_file.exists():
            # 尝试 .db 后缀
            db_file = db_dir / f"{db_id}.db"
        if not db_file.exists():
            raise FileNotFoundError(f"未找到数据库文件：{db_dir}/{db_id}.sqlite 或 .db")

        print(f"[INFO] 正在为 {db_id} 构建 LSH 索引...")

        # 1. 提取唯一值
        unique_values = self.get_unique_values(str(db_file))
        total_values = sum(
            len(v) for tv in unique_values.values() for v in tv.values()
        )
        print(f"[INFO] 提取到 {total_values} 个唯一值（{len(unique_values)} 张表）")

        # 2. 构建 LSH 索引
        lsh, minhashes = self.build_index(unique_values, verbose=verbose)
        print(f"[INFO] LSH 索引构建完成，共 {len(minhashes)} 个条目")

        # 3. 保存到文件
        preprocessed_dir = db_dir / "preprocessed"
        preprocessed_dir.mkdir(exist_ok=True)

        with open(preprocessed_dir / f"{db_id}_lsh.pkl", "wb") as f:
            pickle.dump(lsh, f)
        with open(preprocessed_dir / f"{db_id}_minhashes.pkl", "wb") as f:
            pickle.dump(minhashes, f)
        with open(preprocessed_dir / f"{db_id}_unique_values.pkl", "wb") as f:
            pickle.dump(unique_values, f)

        print(f"[INFO] 索引已保存到 {preprocessed_dir}")

    @staticmethod
    def load_db_lsh(db_directory_path: str) -> Tuple[MinHashLSH, Dict[str, Tuple[MinHash, str, str, str]]]:
        """
        从文件加载已构建的 LSH 索引

        Args:
            db_directory_path: 数据库目录路径

        Returns:
            Tuple[MinHashLSH, Dict]: (LSH 索引, minhashes 字典)

        示例:
        ```python
        lsh, minhashes = LSHIndexer.load_db_lsh("data/formula_1/")
        results = LSHIndexer().query(lsh, minhashes, "Hamilton")
        ```
        """
        db_dir = Path(db_directory_path)
        db_id = db_dir.name
        preprocessed_dir = db_dir / "preprocessed"

        lsh_path = preprocessed_dir / f"{db_id}_lsh.pkl"
        minhashes_path = preprocessed_dir / f"{db_id}_minhashes.pkl"

        if not lsh_path.exists() or not minhashes_path.exists():
            raise FileNotFoundError(
                f"LSH 索引文件不存在，请先运行 build_db_lsh()\n"
                f"缺少: {lsh_path} 或 {minhashes_path}"
            )

        with open(lsh_path, "rb") as f:
            lsh = pickle.load(f)
        with open(minhashes_path, "rb") as f:
            minhashes = pickle.load(f)

        return lsh, minhashes

    @staticmethod
    def is_lsh_built(db_directory_path: str) -> bool:
        """
        检查 LSH 索引是否已构建

        Args:
            db_directory_path: 数据库目录路径

        Returns:
            bool: 索引是否存在
        """
        db_dir = Path(db_directory_path)
        db_id = db_dir.name
        preprocessed_dir = db_dir / "preprocessed"

        lsh_path = preprocessed_dir / f"{db_id}_lsh.pkl"
        minhashes_path = preprocessed_dir / f"{db_id}_minhashes.pkl"

        return lsh_path.exists() and minhashes_path.exists()

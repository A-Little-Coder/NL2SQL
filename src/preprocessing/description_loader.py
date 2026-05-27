# ============================================================================
# Schema 描述信息加载器
# ============================================================================
# 功能说明:
#   从 database_description/*.csv 文件加载表和列的描述信息
#   参考 CHESS 项目的 csv_utils.py 实现，适配本项目需求
#
# 输入:
#   - db_directory_path: 数据库所在目录路径
#
# 输出:
#   - 返回 {table_name: {column_name: {column_description, ...}}} 结构
#
# 使用方法:
#   loader = DescriptionLoader("data/formula_1/")
#   descriptions = loader.load_tables_description()
#   desc = loader.get_column_description("races", "raceId")
#
# ============================================================================


from pathlib import Path
from typing import Dict, Optional
import csv


class DescriptionLoader:
    """
    从 database_description/*.csv 文件加载表和列的描述信息

    BIRD-SQL 数据集为每个数据库提供了详细的元数据描述文件，
    这些文件位于 database_description/ 目录下，每个表对应一个 CSV 文件。

    CSV 文件格式:
    ┌──────────────────────┬───────────────┬─────────────────────┬──────────────┬──────────────────┐
    │ original_column_name │ column_name   │ column_description  │ data_format  │ value_description│
    ├──────────────────────┼───────────────┼─────────────────────┼──────────────┼──────────────────┤
    │ raceId               │ race ID       │ unique id of race   │ integer      │                  │
    │ circuitId            │ Circuit Id    │ Circuit Id          │ integer      │                  │
    └──────────────────────┴───────────────┴─────────────────────┴──────────────┴──────────────────┘

    Attributes:
        db_directory_path (Path): 数据库目录路径
        description_path (Path): 描述文件目录路径
        _cache (dict): 缓存已加载的描述信息，避免重复读取
    """

    def __init__(self, db_directory_path: str):
        """
        初始化描述加载器

        Args:
            db_directory_path: 数据库所在目录路径
                            例如："data/formula_1/"

        使用示例:
        ```python
        loader = DescriptionLoader("data/formula_1/")
        descriptions = loader.load_tables_description()
        ```
        """
        self.db_directory_path = Path(db_directory_path)
        self.description_path = self.db_directory_path / "database_description"
        self._cache: Dict[str, Dict[str, Dict[str, str]]] = {}

    def load_tables_description(self) -> Dict[str, Dict[str, Dict[str, str]]]:
        """
        加载所有表的列描述信息

        从 database_description/*.csv 文件中读取每个表的列描述，
        支持多种编码格式（utf-8-sig, cp1252）以适应不同来源的数据。

        Returns:
            Dict[str, Dict[str, Dict[str, str]]]: 描述信息字典
                {
                    "races": {
                        "raceid": {
                            "original_column_name": "raceId",
                            "column_name": "race ID",
                            "column_description": "unique identification number identifying the race",
                            "data_format": "integer",
                            "value_description": ""
                        },
                        "circuitid": {...}
                    },
                    "drivers": {...}
                }

        使用示例:
        ```python
        loader = DescriptionLoader("data/formula_1/")
        descriptions = loader.load_tables_description()

        # 访问 races 表的 raceId 列描述
        races_desc = descriptions.get("races", {})
        race_id_info = races_desc.get("raceid", {})
        print(race_id_info.get("column_description"))
        # 输出："the unique identification number identifying the race"
        ```

        注意:
            - 使用缓存机制，首次加载后后续调用直接返回缓存结果
            - 自动跳过无法读取的 CSV 文件
            - 列名统一转换为小写以便匹配
        """
        # 检查缓存
        if self._cache:
            return self._cache

        self._cache = {}

        # 检查描述目录是否存在
        if not self.description_path.exists():
            return {}

        # 编码尝试顺序
        encoding_types = ['utf-8-sig', 'cp1252']

        for csv_file in self.description_path.glob("*.csv"):
            # 表名取自文件名（不含扩展名），转小写
            table_name = csv_file.stem.lower().strip()
            self._cache[table_name] = {}

            could_read = False
            for encoding_type in encoding_types:
                try:
                    with open(csv_file, 'r', encoding=encoding_type) as f:
                        reader = csv.DictReader(f)
                        for row in reader:
                            # 获取原始列名（SQLite 中的实际列名）
                            column_name = row.get('original_column_name', '').strip()
                            if not column_name:
                                continue

                            # 清理换行符等特殊字符
                            column_description = row.get('column_description', '')
                            if column_description:
                                column_description = column_description.replace('\n', ' ').strip()

                            value_description = row.get('value_description', '')
                            if value_description:
                                value_description = value_description.replace('\n', ' ').strip()

                            self._cache[table_name][column_name.lower().strip()] = {
                                "original_column_name": column_name,
                                "column_name": row.get('column_name', '').strip(),
                                "column_description": column_description,
                                "data_format": row.get('data_format', '').strip(),
                                "value_description": value_description
                            }

                    could_read = True
                    break

                except Exception:
                    # 尝试下一种编码
                    continue

            if not could_read:
                # 记录但继续处理其他文件
                pass

        return self._cache

    def get_column_description(self, table_name: str, column_name: str) -> Optional[str]:
        """
        获取单个列的描述信息

        Args:
            table_name: 表名，例如 "races"
            column_name: 列名，例如 "raceId"

        Returns:
            Optional[str]: 列的描述文本，如果不存在则返回 None

        优先级:
            1. column_description（首选）
            2. original_column_name（回退）

        使用示例:
        ```python
        loader = DescriptionLoader("data/formula_1/")

        # 获取 races 表中 raceId 列的描述
        desc = loader.get_column_description("races", "raceId")
        print(desc)
        # 输出："the unique identification number identifying the race"
        ```
        """
        tables = self.load_tables_description()
        table_desc = tables.get(table_name.lower(), {})
        col_desc = table_desc.get(column_name.lower(), {})

        # 优先返回 column_description，否则返回 original_column_name
        return col_desc.get("column_description") or col_desc.get("original_column_name")

    def clear_cache(self):
        """
        清空缓存，强制重新加载描述文件

        在描述文件被外部修改后可以使用此方法：
        ```python
        loader.clear_cache()
        descriptions = loader.load_tables_description()  # 重新读取
        ```
        """
        self._cache = {}

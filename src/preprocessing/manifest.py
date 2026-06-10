# ============================================================================
# Manifest 清单管理 — 预处理增量更新的快照基准
# ============================================================================
# 负责:
#   1. Manifest 文件的加载与保存（原子写入）
#   2. 当前 DB schema 与 Manifest 的 diff 计算
#   3. 从 schema 构建 Manifest 条目
#
# Manifest 存储路径: data/manifest.json
# ============================================================================


import json
import os
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


# ============================================================================
# 数据结构
# ============================================================================


@dataclass
class ColumnInfo:
    """单列的 Manifest 信息"""
    type: str
    is_fk: bool = False
    references: Optional[str] = None  # "table.column" 格式


@dataclass
class TableManifest:
    """单表的 Manifest 信息"""
    columns: Dict[str, ColumnInfo]  # col_name -> ColumnInfo


@dataclass
class DatabaseManifest:
    """单库的 Manifest 信息"""
    schema_index_build_time: Optional[str] = None
    schema_graph_build_time: Optional[str] = None
    lsh_index_build_time: Optional[str] = None
    tables: Dict[str, TableManifest] = field(default_factory=dict)  # table_name -> TableManifest


@dataclass
class ManifestData:
    """完整的 Manifest 数据"""
    version: int = 1
    last_updated: str = ""
    databases: Dict[str, DatabaseManifest] = field(default_factory=dict)


@dataclass
class TableDiff:
    """单表的差异信息"""
    added_columns: List[str] = field(default_factory=list)
    removed_columns: List[str] = field(default_factory=list)
    changed_columns: Dict[str, Dict[str, str]] = field(default_factory=dict)
    # changed_columns: {col_name: {"old_type": str, "new_type": str}}

    @property
    def has_changes(self) -> bool:
        return bool(self.added_columns or self.removed_columns or self.changed_columns)


@dataclass
class DiffResult:
    """单库的差异结果"""
    db_id: str = ""
    added_tables: List[str] = field(default_factory=list)
    removed_tables: List[str] = field(default_factory=list)
    modified_tables: Dict[str, TableDiff] = field(default_factory=dict)  # table_name -> TableDiff

    @property
    def has_changes(self) -> bool:
        return bool(self.added_tables or self.removed_tables or self.modified_tables)


# ============================================================================
# Manifest 管理器
# ============================================================================


class Manifest:
    """
    Manifest 清单管理器

    负责预处理增量更新的快照基准管理：
    - 全量构建后自动写入 Manifest
    - 增量更新时通过 diff 确定变更范围
    """

    DEFAULT_MANIFEST_PATH = "data/manifest.json"

    def __init__(self, manifest_path: str = None):
        """
        Args:
            manifest_path: Manifest 文件路径，默认 data/manifest.json
        """
        self.manifest_path = manifest_path or self.DEFAULT_MANIFEST_PATH

    # ------------------------------------------------------------------
    # 加载 / 保存
    # ------------------------------------------------------------------

    def load(self) -> ManifestData:
        """加载 Manifest 文件，不存在时返回空 ManifestData"""
        path = Path(self.manifest_path)
        if not path.exists():
            logger.info(f"Manifest 文件不存在，返回空数据: {self.manifest_path}")
            return ManifestData()

        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)

            databases = {}
            for db_id, db_raw in raw.get("databases", {}).items():
                tables = {}
                for tbl_name, tbl_raw in db_raw.get("tables", {}).items():
                    columns = {}
                    for col_name, col_raw in tbl_raw.get("columns", {}).items():
                        columns[col_name] = ColumnInfo(
                            type=col_raw.get("type", "UNKNOWN"),
                            is_fk=col_raw.get("is_fk", False),
                            references=col_raw.get("references"),
                        )
                    tables[tbl_name] = TableManifest(columns=columns)
                databases[db_id] = DatabaseManifest(
                    schema_index_build_time=db_raw.get("schema_index_build_time"),
                    schema_graph_build_time=db_raw.get("schema_graph_build_time"),
                    lsh_index_build_time=db_raw.get("lsh_index_build_time"),
                    tables=tables,
                )

                # 兼容旧格式：build_time 存在时填充三个字段
                if "build_time" in db_raw and db_raw["build_time"]:
                    old_time = db_raw["build_time"]
                    if databases[db_id].schema_index_build_time is None:
                        databases[db_id].schema_index_build_time = old_time
                    if databases[db_id].schema_graph_build_time is None:
                        databases[db_id].schema_graph_build_time = old_time
                    if databases[db_id].lsh_index_build_time is None:
                        databases[db_id].lsh_index_build_time = old_time

            return ManifestData(
                version=raw.get("version", 1),
                last_updated=raw.get("last_updated", ""),
                databases=databases,
            )

        except Exception as e:
            logger.error(f"加载 Manifest 失败: {e}")
            return ManifestData()

    def save(self, data: ManifestData) -> bool:
        """
        原子写入 Manifest 文件（先写 tmp 再 rename）

        Args:
            data: ManifestData 实例

        Returns:
            bool: 保存成功返回 True
        """
        # 序列化
        raw = self._to_dict(data)

        try:
            os.makedirs(os.path.dirname(self.manifest_path), exist_ok=True)

            # 原子写入：写临时文件 → rename 覆盖
            fd, tmp_path = tempfile.mkstemp(
                dir=os.path.dirname(self.manifest_path),
                prefix="manifest_",
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(raw, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, self.manifest_path)
            except Exception:
                # 清理临时文件
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise

            logger.info(f"Manifest 已保存: {self.manifest_path}")
            return True

        except Exception as e:
            logger.error(f"保存 Manifest 失败: {e}")
            return False

    @staticmethod
    def _to_dict(data: ManifestData) -> dict:
        """将 ManifestData 序列化为 dict"""
        databases = {}
        for db_id, db_mf in data.databases.items():
            tables = {}
            for tbl_name, tbl_mf in db_mf.tables.items():
                columns = {}
                for col_name, col_info in tbl_mf.columns.items():
                    col_entry = {"type": col_info.type}
                    if col_info.is_fk:
                        col_entry["is_fk"] = True
                    if col_info.references:
                        col_entry["references"] = col_info.references
                    columns[col_name] = col_entry
                tables[tbl_name] = {"columns": columns}
            db_entry = {"tables": tables}
            if db_mf.schema_index_build_time is not None:
                db_entry["schema_index_build_time"] = db_mf.schema_index_build_time
            if db_mf.schema_graph_build_time is not None:
                db_entry["schema_graph_build_time"] = db_mf.schema_graph_build_time
            if db_mf.lsh_index_build_time is not None:
                db_entry["lsh_index_build_time"] = db_mf.lsh_index_build_time
            databases[db_id] = db_entry

        return {
            "version": data.version,
            "last_updated": data.last_updated,
            "databases": databases,
        }

    # ------------------------------------------------------------------
    # 从 schema 构建 Manifest 条目
    # ------------------------------------------------------------------

    @staticmethod
    def build_manifest_from_schema(db_id: str, all_schemas: dict) -> DatabaseManifest:
        """
        从 DB schema 构建单库 Manifest 条目

        Args:
            db_id: 数据库 ID
            all_schemas: {table_name: schema_dict}，来自 DatabaseConnector.get_all_schemas()

        Returns:
            DatabaseManifest: 该库的 Manifest 条目
        """
        tables = {}
        for table_name, schema in all_schemas.items():
            columns = {}
            # 收集 FK 信息：col_name -> references_table.references_column
            fk_map = {}
            for fk in schema.get("foreign_keys", []):
                fk_map[fk["column"]] = f"{fk['references_table']}.{fk['references_column']}"

            for col in schema.get("columns", []):
                col_name = col["name"]
                col_type = col.get("type", "UNKNOWN")
                is_fk = col_name in fk_map
                ref = fk_map.get(col_name)

                columns[col_name] = ColumnInfo(
                    type=col_type,
                    is_fk=is_fk,
                    references=ref,
                )

            tables[table_name] = TableManifest(columns=columns)

        return DatabaseManifest(tables=tables)

    # ------------------------------------------------------------------
    # Diff 计算
    # ------------------------------------------------------------------

    @staticmethod
    def compute_diff(
        old_entry: Optional[DatabaseManifest],
        current_schemas: Dict[str, dict],
    ) -> DiffResult:
        """
        对比 Manifest 中的旧 schema 与当前 schema，计算差异

        Args:
            old_entry: Manifest 中该库的旧条目，None 表示全新库
            current_schemas: {table_name: schema_dict}，来自 DatabaseConnector.get_all_schemas()

        Returns:
            DiffResult: 差异结果
        """
        diff = DiffResult()

        if old_entry is None:
            # 全新库，所有表都是新增
            diff.added_tables = sorted(current_schemas.keys())
            return diff

        old_tables = set(old_entry.tables.keys())
        current_tables = set(current_schemas.keys())

        diff.added_tables = sorted(current_tables - old_tables)
        diff.removed_tables = sorted(old_tables - current_tables)

        # 检查共同表的列变化
        common_tables = old_tables & current_tables
        for table_name in sorted(common_tables):
            table_diff = Manifest._diff_table(
                old_entry.tables.get(table_name),
                current_schemas[table_name],
            )
            if table_diff.has_changes:
                diff.modified_tables[table_name] = table_diff

        return diff

    @staticmethod
    def _diff_table(
        old_table: Optional[TableManifest],
        current_schema: dict,
    ) -> TableDiff:
        """
        对比单表的列差异

        Args:
            old_table: Manifest 中该表的旧条目，None 表示全新表
            current_schema: 当前 schema dict

        Returns:
            TableDiff: 表级差异
        """
        diff = TableDiff()

        if old_table is None:
            # 全新表，所有列都是新增
            for col in current_schema.get("columns", []):
                diff.added_columns.append(col["name"])
            return diff

        old_columns = set(old_table.columns.keys())
        current_columns = {col["name"] for col in current_schema.get("columns", [])}

        diff.added_columns = sorted(current_columns - old_columns)
        diff.removed_columns = sorted(old_columns - current_columns)

        # 检查共同列的类型变化
        common_cols = old_columns & current_columns
        current_col_map = {col["name"]: col for col in current_schema.get("columns", [])}
        for col_name in sorted(common_cols):
            old_type = old_table.columns[col_name].type
            new_type = current_col_map[col_name].get("type", "UNKNOWN")
            if old_type.upper() != new_type.upper():
                diff.changed_columns[col_name] = {
                    "old_type": old_type,
                    "new_type": new_type,
                }

        return diff

    # ------------------------------------------------------------------
    # 便捷方法
    # ------------------------------------------------------------------

    def get_db_entry(self, db_id: str) -> Optional[DatabaseManifest]:
        """获取 Manifest 中指定库的条目"""
        return self.load().databases.get(db_id)

    def update_db(self, db_id: str, entry: DatabaseManifest) -> bool:
        """更新 Manifest 中指定库的条目"""
        data = self.load()
        data.databases[db_id] = entry
        data.last_updated = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        return self.save(data)

    def update_build_time(
        self,
        db_id: str,
        module: str,
        build_time: str = None,
    ) -> bool:
        """
        更新指定库的某个模块的 build_time

        Args:
            db_id: 数据库 ID
            module: 模块名，"schema_index" / "schema_graph" / "lsh_index"
            build_time: 构建时间，默认当前时间

        Returns:
            bool: 更新成功返回 True
        """
        if build_time is None:
            build_time = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

        data = self.load()
        if db_id not in data.databases:
            # 库不存在，先创建空条目
            data.databases[db_id] = DatabaseManifest()

        entry = data.databases[db_id]
        field_map = {
            "schema_index": "schema_index_build_time",
            "schema_graph": "schema_graph_build_time",
            "lsh_index": "lsh_index_build_time",
        }
        attr = field_map.get(module)
        if attr is None:
            logger.warning(f"未知模块名: {module}")
            return False

        setattr(entry, attr, build_time)
        data.last_updated = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        return self.save(data)

    def remove_db(self, db_id: str) -> bool:
        """从 Manifest 中删除指定库的条目"""
        data = self.load()
        if db_id in data.databases:
            del data.databases[db_id]
            data.last_updated = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            return self.save(data)
        return True

    def get_all_db_ids(self) -> List[str]:
        """获取 Manifest 中所有已记录的库 ID"""
        return sorted(self.load().databases.keys())


# ============================================================================
# 便捷函数：供构建脚本调用
# ============================================================================


def write_manifest_for_db(
    db_id: str,
    data_dir: str,
    manifest_path: str = None,
    module: str = None,
) -> bool:
    """
    为指定数据库构建/更新 Manifest 条目

    供构建脚本（build_schema_index / build_schema_graphs / build_lsh_index）在
    全量构建完成后调用。

    Args:
        db_id: 数据库 ID
        data_dir: 数据目录
        manifest_path: Manifest 文件路径（默认 data/manifest.json）
        module: 模块名，"schema_index" / "schema_graph" / "lsh_index"
                指定时只更新该模块的 build_time；不指定时重建完整条目

    Returns:
        bool: 写入成功返回 True
    """
    try:
        manifest = Manifest(manifest_path=manifest_path)

        if module:
            # 只更新指定模块的 build_time
            data = manifest.load()
            if db_id not in data.databases:
                # 库条目不存在，先构建 schema 快照
                from src.preprocessing.database_connector import DatabaseConnector
                from src.preprocessing.build_schema_index import find_bird_databases
                db_map = find_bird_databases(data_dir)
                db_path = db_map.get(db_id)
                if not db_path:
                    logger.warning(f"未找到数据库文件: {db_id}，跳过 Manifest 写入")
                    return False
                connector = DatabaseConnector(db_path, db_type="sqlite")
                all_schemas = connector.get_all_schemas()
                entry = Manifest.build_manifest_from_schema(db_id, all_schemas)
                connector.disconnect()
                data.databases[db_id] = entry
                manifest.save(data)

            return manifest.update_build_time(db_id, module)
        else:
            # 无 module 时：重建完整条目（兼容旧调用）
            from src.preprocessing.database_connector import DatabaseConnector
            from src.preprocessing.build_schema_index import find_bird_databases
            db_map = find_bird_databases(data_dir)
            db_path = db_map.get(db_id)
            if not db_path:
                logger.warning(f"未找到数据库文件: {db_id}，跳过 Manifest 写入")
                return False

            connector = DatabaseConnector(db_path, db_type="sqlite")
            all_schemas = connector.get_all_schemas()
            entry = Manifest.build_manifest_from_schema(db_id, all_schemas)
            connector.disconnect()

            # 保留已有模块的 build_time
            data = manifest.load()
            old_entry = data.databases.get(db_id)
            if old_entry:
                if old_entry.schema_index_build_time and not entry.schema_index_build_time:
                    entry.schema_index_build_time = old_entry.schema_index_build_time
                if old_entry.schema_graph_build_time and not entry.schema_graph_build_time:
                    entry.schema_graph_build_time = old_entry.schema_graph_build_time
                if old_entry.lsh_index_build_time and not entry.lsh_index_build_time:
                    entry.lsh_index_build_time = old_entry.lsh_index_build_time

            return manifest.update_db(db_id, entry)

    except Exception as e:
        logger.warning(f"写入 Manifest 失败 ({db_id}): {e}")
        return False

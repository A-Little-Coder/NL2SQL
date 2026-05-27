# ============================================================================
# 数据库连接管理器
# ============================================================================
# 功能说明:
#   负责连接到不同类型的数据库（SQLite、MySQL），提供统一的接口进行数据访问
#
# 输入:
#   - db_path: 数据库文件路径（SQLite）或主机地址（MySQL）
#   - db_type: 数据库类型，支持 "sqlite" 或 "mysql"
#   - credentials: 可选的认证信息（MySQL 需要）
#
# 输出:
#   - 返回数据库连接对象
#   - 提供 get_tables(), get_schema(), execute_query() 等方法
# ============================================================================


import sqlite3
from typing import List, Dict, Any, Tuple, Optional
from contextlib import contextmanager


class DatabaseConnector:
    """
    数据库连接管理器 - 统一不同数据库类型的访问接口

    支持以下数据库类型：
    - SQLite: 本地文件数据库，Python 内置支持
    - MySQL: 需要安装 mysql-connector-python

    使用示例:
    ```python
    # SQLite 使用示例
    connector = DatabaseConnector("data/mydb.db", db_type="sqlite")
    tables = connector.get_tables()
    schema = connector.get_table_schema("users")
    success, result, error = connector.execute_query("SELECT * FROM users")

    # MySQL 使用示例
    connector = DatabaseConnector(
        "localhost",
        db_type="mysql",
        credentials={"user": "root", "password": "secret", "database": "mydb"}
    )
    ```

    Attributes:
        db_path (str): 数据库路径
        db_type (str): 数据库类型 ('sqlite' | 'mysql')
        credentials (dict): 认证信息
        connection: 数据库连接对象
    """

    def __init__(self, db_path: str, db_type: str = "sqlite", credentials: dict = None):
        """
        初始化数据库连接器

        Args:
            db_path: 数据库文件路径（SQLite）或主机地址（MySQL）
            db_type: 数据库类型，默认 SQLite
            credentials: 认证信息字典
                       SQLite: 不需要
                       MySQL: {'user': ..., 'password': ..., 'database': ...}

        注意:
            - SQLite 会尝试自动检测并创建不存在的数据库文件
            - MySQL 需要在 credentials 中提供完整的连接信息
        """
        self.db_path = db_path
        self.db_type = db_type.lower()
        self.credentials = credentials or {}
        self.connection = None
        self._connect()

    def _connect(self) -> bool:
        """
        建立数据库连接（内部方法）

        Returns:
            bool: 连接成功返回 True，失败返回 False
        """
        try:
            if self.db_type == "sqlite":
                # SQLite 连接
                self.connection = sqlite3.connect(self.db_path, check_same_thread=False)
                self.connection.row_factory = sqlite3.Row  # 允许通过列名访问
                return True

            elif self.db_type == "mysql":
                # MySQL 连接
                try:
                    import mysql.connector
                except ImportError:
                    raise ImportError("请安装 MySQL 连接器：pip install mysql-connector-python")

                self.connection = mysql.connector.connect(
                    host=self.credentials.get('host', self.db_path),
                    port=self.credentials.get('port', 3306),
                    user=self.credentials.get('user'),
                    password=self.credentials.get('password'),
                    database=self.credentials.get('database'),
                    charset='utf8mb4'
                )
                return True

            else:
                raise ValueError(f"不支持的数据库类型：{self.db_type}")

        except Exception as e:
            print(f"[警告] 数据库连接失败：{e}")
            return False

    def connect(self) -> bool:
        """
        建立数据库连接

        Returns:
            bool: 连接成功返回 True，失败返回 False

        用法:
        ```python
        connector = DatabaseConnector("data/test.db")
        if connector.connect():
            print("连接成功")
        ```
        """
        if self.connection is None:
            return self._connect()
        try:
            # 测试连接是否仍然有效
            if self.db_type == "sqlite":
                self.connection.cursor().execute("SELECT 1")
            else:
                self.connection.ping(reconnect=False)
            return True
        except Exception:
            # 连接失效，重新连接
            return self._connect()

    def disconnect(self):
        """
        关闭数据库连接

        用法:
        ```python
        connector.disconnect()
        ```

        注意:
            - 会自动提交未提交的更改
            - 调用后需要重新 connect 才能继续使用
        """
        if self.connection:
            try:
                if self.db_type == "sqlite":
                    self.connection.commit()
                self.connection.close()
            except Exception as e:
                print(f"[警告] 关闭连接时出错：{e}")
            finally:
                self.connection = None

    @contextmanager
    def _get_cursor(self):
        """
        获取游标（上下文管理器）

        用法:
        ```python
        with connector._get_cursor() as cursor:
            cursor.execute("SELECT * FROM users")
            results = cursor.fetchall()
        ```

        注意:
            - 自动处理异常时的回滚
            - 自动关闭游标
        """
        cursor = None
        try:
            self.connect()
            cursor = self.connection.cursor()
            yield cursor
            if self.db_type == "sqlite":
                self.connection.commit()
        except Exception as e:
            if self.db_type == "sqlite":
                self.connection.rollback()
            raise e
        finally:
            if cursor:
                cursor.close()

    def get_tables(self) -> List[str]:
        """
        获取数据库中所有表的列表

        Returns:
            list: 表名列表，如 ['users', 'orders', 'products']

        用法:
        ```python
        tables = connector.get_tables()
        for table in tables:
            print(f"表：{table}")
        ```

        注意:
            - SQLite: 排除系统表（sqlite_开头的表）
            - MySQL: 返回当前数据库中的所有表
        """
        try:
            if self.db_type == "sqlite":
                query = """
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name NOT LIKE 'sqlite_%'
                    ORDER BY name
                """
                with self._get_cursor() as cursor:
                    cursor.execute(query)
                    return [row[0] for row in cursor.fetchall()]

            elif self.db_type == "mysql":
                query = "SHOW TABLES"
                with self._get_cursor() as cursor:
                    cursor.execute(query)
                    # MySQL 的 SHOW TABLES 返回元组列表
                    return [row[0] for row in cursor.fetchall()]

            else:
                return []

        except Exception as e:
            print(f"[错误] 获取表列表失败：{e}")
            return []

    def get_table_schema(self, table_name: str, sample_size: int = 5) -> Dict[str, Any]:
        """
        获取指定表的 schema 信息

        Args:
            table_name: 表名
            sample_size: 每个列采样的样本数量，默认 5 个

        Returns:
            dict: 包含列信息的字典
            {
                "table_name": "users",
                "columns": [
                    {
                        "name": "id",
                        "type": "INTEGER",
                        "primary_key": True,
                        "nullable": False,
                        "default": None
                    },
                    {
                        "name": "name",
                        "type": "TEXT",
                        "primary_key": False,
                        "nullable": True,
                        "default": None
                    }
                ],
                "foreign_keys": [
                    {
                        "column": "department_id",
                        "references_table": "departments",
                        "references_column": "id"
                    }
                ],
                "sample_values": {
                    "id": [1, 2, 3, 4, 5],
                    "name": ["Alice", "Bob", "Charlie", "David", "Eve"]
                },
                "row_count": 1000
            }

        用法:
        ```python
        schema = connector.get_table_schema("users")
        for col in schema["columns"]:
            print(f"{col['name']} ({col['type']})")
        ```
        """
        schema = {
            "table_name": table_name,
            "columns": [],
            "foreign_keys": [],
            "sample_values": {},
            "row_count": 0
        }

        try:
            if self.db_type == "sqlite":
                # 获取列信息
                pragma_query = f"PRAGMA table_info({table_name})"
                with self._get_cursor() as cursor:
                    cursor.execute(pragma_query)
                    for row in cursor.fetchall():
                        # row: (cid, name, type, notnull, dflt_value, pk)
                        column = {
                            "name": row[1],
                            "type": row[2] or "TEXT",
                            "primary_key": bool(row[5]),
                            "nullable": not bool(row[3]),
                            "default": row[4]
                        }
                        schema["columns"].append(column)

                # 获取外键信息
                fk_query = f"PRAGMA foreign_key_list({table_name})"
                with self._get_cursor() as cursor:
                    cursor.execute(fk_query)
                    for row in cursor.fetchall():
                        # row: (id, seq, table, from, to, on_update, on_delete, match)
                        schema["foreign_keys"].append({
                            "column": row[3],
                            "references_table": row[2],
                            "references_column": row[4]
                        })

                # 获取样本值
                for col in schema["columns"]:
                    col_name = col["name"]
                    sample_query = f"SELECT DISTINCT `{col_name}` FROM `{table_name}` LIMIT {sample_size}"
                    with self._get_cursor() as cursor:
                        cursor.execute(sample_query)
                        samples = [row[0] for row in cursor.fetchall()]
                        if samples:
                            schema["sample_values"][col_name] = samples

                # 获取行数
                count_query = f"SELECT COUNT(*) FROM `{table_name}`"
                with self._get_cursor() as cursor:
                    cursor.execute(count_query)
                    schema["row_count"] = cursor.fetchone()[0]

            elif self.db_type == "mysql":
                # 获取列信息
                describe_query = f"DESCRIBE `{table_name}`"
                with self._get_cursor() as cursor:
                    cursor.execute(describe_query)
                    for row in cursor.fetchall():
                        # row: (Field, Type, Null, Key, Default, Extra)
                        column = {
                            "name": row[0],
                            "type": row[1],
                            "primary_key": row[3] == "PRI",
                            "nullable": row[2] == "YES",
                            "default": row[4]
                        }
                        schema["columns"].append(column)

                # 获取外键信息
                fk_query = f"""
                    SELECT
                        kcu.COLUMN_NAME,
                        kcu.REFERENCED_TABLE_NAME,
                        kcu.REFERENCED_COLUMN_NAME
                    FROM information_schema.KEY_COLUMN_USAGE kcu
                    WHERE kcu.TABLE_SCHEMA = DATABASE()
                      AND kcu.TABLE_NAME = '{table_name}'
                      AND kcu.REFERENCED_TABLE_NAME IS NOT NULL
                """
                with self._get_cursor() as cursor:
                    cursor.execute(fk_query)
                    for row in cursor.fetchall():
                        schema["foreign_keys"].append({
                            "column": row[0],
                            "references_table": row[1],
                            "references_column": row[2]
                        })

                # 获取样本值
                for col in schema["columns"]:
                    col_name = col["name"]
                    sample_query = f"SELECT DISTINCT `{col_name}` FROM `{table_name}` LIMIT {sample_size}"
                    with self._get_cursor() as cursor:
                        cursor.execute(sample_query)
                        samples = [row[0] for row in cursor.fetchall()]
                        if samples:
                            schema["sample_values"][col_name] = samples

                # 获取行数
                count_query = f"SELECT COUNT(*) FROM `{table_name}`"
                with self._get_cursor() as cursor:
                    cursor.execute(count_query)
                    schema["row_count"] = cursor.fetchone()[0]

        except Exception as e:
            print(f"[错误] 获取表 schema 失败：{e}")

        return schema

    def execute_query(self, sql: str, timeout: int = 30) -> Tuple[bool, Any, str]:
        """
        执行 SQL 查询并返回结果

        Args:
            sql: SQL 查询语句
            timeout: 超时时间（秒），默认 30 秒

        Returns:
            tuple: (success, result, error)
            - success: 执行是否成功 (True/False)
            - result: 查询结果（成功时为列表，失败时为 None）
                     成功时格式：[(col1, col2, ...), (col1, col2, ...), ...]
            - error: 错误信息（失败时）或 None（成功时）

        用法:
        ```python
        # SELECT 查询
        success, rows, error = connector.execute_query("SELECT * FROM users WHERE id = 1")
        if success:
            for row in rows:
                print(row)

        # INSERT/UPDATE/DELETE 查询
        success, result, error = connector.execute_query("INSERT INTO users VALUES (...)")
        if success:
            print(f"影响了{result}行")
        ```
        """
        try:
            if self.db_type == "sqlite":
                # SQLite 设置超时
                self.connection.execute(f"PRAGMA busy_timeout = {timeout * 1000}")

            with self._get_cursor() as cursor:
                cursor.execute(sql)

                # 检查是否为查询语句（返回结果）
                if self.db_type == "sqlite":
                    description = cursor.description
                else:
                    description = cursor.column_names

                if description:
                    # SELECT 查询，返回所有行
                    rows = cursor.fetchall()
                    return True, rows, None
                else:
                    # INSERT/UPDATE/DELETE，返回影响行数
                    if self.db_type == "sqlite":
                        affected = cursor.rowcount
                    else:
                        affected = cursor.rowcount
                    return True, affected, None

        except Exception as e:
            error_msg = str(e)
            return False, None, error_msg

    def explain_query(self, sql: str) -> Tuple[bool, str, str]:
        """
        对 SQL 查询执行 EXPLAIN 分析

        Args:
            sql: SQL 查询语句

        Returns:
            tuple: (success, explain_output, error)
            - success: 分析是否成功
            - explain_output: 执行计划输出（字符串形式）
            - error: 错误信息（失败时）或 None（成功时）

        用途: 在执行真实查询前验证 SQL 语法的正确性

        用法:
        ```python
        success, plan, error = connector.explain_query("SELECT * FROM users u JOIN orders o ON u.id = o.user_id")
        if success:
            print("执行计划:")
            print(plan)
        else:
            print(f"SQL 语法错误：{error}")
        ```

        注意:
            - SQLite: 使用 "EXPLAIN QUERY PLAN"
            - MySQL: 使用 "EXPLAIN"
            - 如果语法错误，explain 会失败
        """
        try:
            if self.db_type == "sqlite":
                explain_sql = f"EXPLAIN QUERY PLAN {sql}"
            elif self.db_type == "mysql":
                explain_sql = f"EXPLAIN {sql}"
            else:
                return False, "", f"不支持的数据库类型：{self.db_type}"

            success, result, error = self.execute_query(explain_sql)
            if not success:
                return False, "", error

            # 将结果格式化为字符串
            if isinstance(result, list) and result:
                # 第一行是列名
                lines = []
                for row in result:
                    lines.append(" | ".join(str(col) for col in row))
                return True, "\n".join(lines), None
            else:
                return True, "无执行计划信息", None

        except Exception as e:
            return False, "", str(e)

    def table_exists(self, table_name: str) -> bool:
        """
        检查表是否存在

        Args:
            table_name: 表名

        Returns:
            bool: 表存在返回 True，否则返回 False

        用法:
        ```python
        if connector.table_exists("users"):
            print("表存在")
        ```
        """
        tables = self.get_tables()
        return table_name in tables

    def get_all_schemas(self) -> Dict[str, Dict[str, Any]]:
        """
        获取所有表的 schema 信息

        Returns:
            dict: {table_name: schema_dict, ...}

        用法:
        ```python
        schemas = connector.get_all_schemas()
        for table_name, schema in schemas.items():
            print(f"\n表：{table_name}")
            for col in schema["columns"]:
                print(f"  - {col['name']} ({col['type']})")
        ```
        """
        schemas = {}
        tables = self.get_tables()
        for table in tables:
            schemas[table] = self.get_table_schema(table)
        return schemas

    def __enter__(self):
        """上下文管理器的进入方法"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器的退出方法，确保连接关闭"""
        self.disconnect()
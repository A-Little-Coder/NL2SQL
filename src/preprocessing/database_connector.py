# ============================================================================
# 数据库连接管理器
# ============================================================================
# 功能说明:
#   负责连接到不同类型的数据库（SQLite、MySQL），提供统一的接口进行数据访问
#
# 输入:
#   - db_path: 数据库文件路径（SQLite）或连接字符串（MySQL）
#   - db_type: 数据库类型，支持 "sqlite" 或 "mysql"
#   - credentials: 可选的认证信息（MySQL 需要）
#
# 输出:
#   - 返回数据库连接对象
#   - 提供 get_tables(), get_schema(), execute_query() 等方法
#
# 待您补充的细节:
#   1. SQLite 连接使用 sqlite3 标准库
#   2. MySQL 连接使用 mysql-connector-python
#   3. 需要考虑连接池和异常处理
# ============================================================================


class DatabaseConnector:
    """
    数据库连接管理器 - 统一不同数据库类型的访问接口

    Attributes:
        db_path (str): 数据库路径
        db_type (str): 数据库类型 ('sqlite' | 'mysql')
        connection: 数据库连接对象
    """

    def __init__(self, db_path: str, db_type: str = "sqlite", credentials: dict = None):
        """
        初始化数据库连接器

        Args:
            db_path: 数据库文件路径（SQLite）或主机地址（MySQL）
            db_type: 数据库类型，默认 SQLite
            credentials: 认证信息字典，MySQL 需要 {'user': ..., 'password': ...}

        TODO: 您需要实现的细节
        - 根据 db_type 选择相应的连接方式
        - 建立数据库连接
        - 测试连接是否成功
        """
        self.db_path = db_path
        self.db_type = db_type
        self.credentials = credentials
        self.connection = None

        # TODO: 实现连接逻辑
        # if db_type == "sqlite":
        #     import sqlite3
        #     self.connection = sqlite3.connect(db_path)
        # elif db_type == "mysql":
        #     import mysql.connector
        #     self.connection = mysql.connector.connect(...)

    def connect(self) -> bool:
        """
        建立数据库连接

        Returns:
            bool: 连接成功返回 True，失败返回 False

        TODO: 您可以添加重试逻辑和超时设置
        """
        pass

    def disconnect(self):
        """
        关闭数据库连接

        TODO: 确保正确释放资源
        """
        pass

    def get_tables(self) -> list:
        """
        获取数据库中所有表的列表

        Returns:
            list: 表名列表，如 ['users', 'orders', 'products']

        TODO:
        - SQLite: 执行 "SELECT name FROM sqlite_master WHERE type='table'"
        - MySQL: 执行 "SHOW TABLES"
        """
        pass

    def get_table_schema(self, table_name: str) -> dict:
        """
        获取指定表的 schema 信息

        Args:
            table_name: 表名

        Returns:
            dict: 包含列信息的字典
            {
                "columns": [
                    {"name": "id", "type": "INTEGER", "primary_key": True},
                    {"name": "name", "type": "TEXT", "primary_key": False},
                    ...
                ],
                "foreign_keys": [...],
                "sample_values": {...}  # 每列的前几个值（用于 LSH 索引）
            }

        TODO:
        - 使用 PRAGMA table_info(table_name) 获取 SQLite 表结构
        - 使用 DESCRIBE table_name 获取 MySQL 表结构
        - 提取外键关系
        - 采样一些数据作为示例值
        """
        pass

    def execute_query(self, sql: str, timeout: int = 30) -> tuple:
        """
        执行 SQL 查询并返回结果

        Args:
            sql: SQL 查询语句
            timeout: 超时时间（秒）

        Returns:
            tuple: (success: bool, result: any, error: str)
            - success: 执行是否成功
            - result: 查询结果（成功时）或 None（失败时）
            - error: 错误信息（失败时）或 None（成功时）

        TODO:
        - 设置查询超时
        - 捕获并分类错误（语法错误、运行时错误等）
        - 返回结构化结果
        """
        pass

    def explain_query(self, sql: str) -> tuple:
        """
        对 SQL 查询执行 EXPLAIN 分析

        Args:
            sql: SQL 查询语句

        Returns:
            tuple: (success: bool, explain_output: str, error: str)

        用途: 在执行真实查询前验证 SQL 语法的正确性

        TODO:
        - SQLite: 执行 "EXPLAIN QUERY PLAN <sql>"
        - MySQL: 执行 "EXPLAIN <sql>"
        - 解析执行计划输出
        """
        pass
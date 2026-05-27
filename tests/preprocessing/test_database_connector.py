# ============================================================================
# DatabaseConnector 测试用例
# ============================================================================
# 功能说明:
#   测试 DatabaseConnector 能否正确连接和查询 BIRD-SQL 数据集的数据库
#
# 目录结构:
#   tests/
#     preprocessing/                    # 与 src/preprocessing 对应
#       test_database_connector.py      # 本测试文件
#
# 使用方法:
#   python -m tests.preprocessing.test_database_connector
#   或
#   pytest tests/preprocessing/test_database_connector.py -v
# ============================================================================


import os
import sys
import unittest
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.preprocessing.database_connector import DatabaseConnector


def find_bird_databases(base_dir: Path = None) -> list:
    """
    查找 BIRD-SQL 数据库文件

    Args:
        base_dir: 基础目录，默认项目 data 目录

    Returns:
        list: 找到的数据库文件路径列表
    """
    if base_dir is None:
        base_dir = Path(__file__).parent.parent.parent / "data"

    databases = []

    # 扫描方式 1: 直接在 base_dir 下查找 .db 和 .sqlite 文件
    for pattern in ["*.db", "*.sqlite"]:
        databases.extend(base_dir.glob(pattern))

    # 扫描方式 2: 查找子目录下的 .sqlite 文件（BIRD-SQL 标准格式）
    # 格式：data/<db_id>/<db_id>.sqlite
    for item in base_dir.iterdir():
        if item.is_dir():
            for db_file in item.glob("*.sqlite"):
                databases.append(db_file)
            # 也检查 .db 文件
            for db_file in item.glob("*.db"):
                databases.append(db_file)

    return sorted(set(databases))


class TestDatabaseConnectorSQLite(unittest.TestCase):
    """SQLite 数据库连接器测试"""

    db_path = None
    connector = None
    available_dbs = None

    @classmethod
    def setUpClass(cls):
        """类级别设置：查找可用的数据库文件"""
        print("\n" + "="*60)
        print("开始测试 DatabaseConnector")
        print("="*60)

        # 查找所有可用的数据库
        cls.available_dbs = find_bird_databases()

        if not cls.available_dbs:
            raise RuntimeError("未找到任何数据库文件！请检查 data/ 目录")

        print(f"\n[INFO] 找到 {len(cls.available_dbs)} 个数据库文件:")
        for db in cls.available_dbs[:5]:
            print(f"  - {db.name}")
        if len(cls.available_dbs) > 5:
            print(f"  ... 还有 {len(cls.available_dbs) - 5} 个")

        # 使用第一个数据库进行测试
        cls.db_path = cls.available_dbs[0]
        print(f"\n[INFO] 使用测试数据库：{cls.db_path}")

        cls.connector = DatabaseConnector(str(cls.db_path), db_type="sqlite")
        print(f"[INFO] 连接成功！")

    def setUp(self):
        """每个测试前的准备工作"""
        self.connector = TestDatabaseConnectorSQLite.connector
        self.db_path = TestDatabaseConnectorSQLite.db_path

    @classmethod
    def tearDownClass(cls):
        """类级别清理"""
        if cls.connector:
            cls.connector.disconnect()
            print("\n[INFO] 连接已关闭")

    def test_01_connect(self):
        """测试 1: 建立数据库连接"""
        print("\n" + "-"*40)
        print("[TEST] 测试连接数据库")
        print("-"*40)

        self.assertIsNotNone(self.connector)
        self.assertIsNotNone(self.connector.connection)
        print(f"[PASS] 连接成功：{self.db_path}")

    def test_02_get_tables(self):
        """测试 2: 获取表列表"""
        print("\n" + "-"*40)
        print("[TEST] 测试获取表列表")
        print("-"*40)

        tables = self.connector.get_tables()
        print(f"[INFO] 找到的表：{tables}")

        self.assertIsInstance(tables, list)
        self.assertTrue(len(tables) > 0, "数据库中应该有表")
        print(f"[PASS] 找到 {len(tables)} 个表")

    def test_03_get_table_schema(self):
        """测试 3: 获取表 Schema"""
        print("\n" + "-"*40)
        print("[TEST] 测试获取表 Schema")
        print("-"*40)

        tables = self.connector.get_tables()
        if not tables:
            self.skipTest("没有可用的表")

        table_name = tables[0]
        print(f"[INFO] 测试表：{table_name}")

        schema = self.connector.get_table_schema(table_name)

        print(f"[INFO] Schema 信息:")
        print(f"  - 表名：{schema.get('table_name')}")
        print(f"  - 列数：{len(schema.get('columns', []))}")
        print(f"  - 外键数：{len(schema.get('foreign_keys', []))}")
        print(f"  - 行数：{schema.get('row_count')}")

        self.assertEqual(schema["table_name"], table_name)
        self.assertIsInstance(schema["columns"], list)
        self.assertTrue(len(schema["columns"]) > 0)

        for col in schema["columns"][:5]:  # 只显示前 5 列
            print(f"  - 列：{col['name']} ({col['type']})")

        print(f"[PASS] Schema 获取成功")

    def test_04_execute_query_select(self):
        """测试 4: 执行 SELECT 查询"""
        print("\n" + "-"*40)
        print("[TEST] 测试 SELECT 查询")
        print("-"*40)

        tables = self.connector.get_tables()
        if not tables:
            self.skipTest("没有可用的表")

        # 使用第一个表进行查询
        table_name = tables[0]
        success, result, error = self.connector.execute_query(
            f"SELECT * FROM `{table_name}` LIMIT 5"
        )

        print(f"[INFO] 查询结果:")
        print(f"  - 成功：{success}")
        print(f"  - 行数：{len(result) if result else 0}")
        if error:
            print(f"  - 错误：{error}")

        self.assertTrue(success, f"查询失败：{error}")

        if result:
            print(f"[INFO] 示例结果：{result[0]}")

        print(f"[PASS] SELECT 查询成功")

    def test_05_explain_query(self):
        """测试 5: EXPLAIN 查询分析"""
        print("\n" + "-"*40)
        print("[TEST] 测试 EXPLAIN 分析")
        print("-"*40)

        tables = self.connector.get_tables()
        if not tables:
            self.skipTest("没有可用的表")

        table_name = tables[0]
        sql = f"SELECT * FROM `{table_name}` WHERE 1=1"
        success, plan, error = self.connector.explain_query(sql)

        print(f"[INFO] SQL: {sql}")
        print(f"[INFO] 执行计划：{'OK' if success else error}")

        self.assertTrue(success, f"EXPLAIN 失败：{error}")
        print(f"[PASS] EXPLAIN 分析成功")

    def test_06_invalid_query(self):
        """测试 6: 无效 SQL 处理"""
        print("\n" + "-"*40)
        print("[TEST] 测试无效 SQL 处理")
        print("-"*40)

        success, result, error = self.connector.execute_query("SELCT * FROM users")

        self.assertFalse(success)
        self.assertIsNone(result)
        self.assertIsNotNone(error)

        print(f"[PASS] 错误处理正确：{error}")

    def test_07_sample_values(self):
        """测试 7: 样本值"""
        print("\n" + "-"*40)
        print("[TEST] 测试样本值")
        print("-"*40)

        tables = self.connector.get_tables()
        if not tables:
            self.skipTest("没有可用的表")

        schema = self.connector.get_table_schema(tables[0])

        print(f"[INFO] 样本值示例:")
        for col_name, values in list(schema.get("sample_values", {}).items())[:3]:
            print(f"  - {col_name}: {values[:3]}")

        print(f"[PASS] 样本值获取成功")

    def test_08_get_all_schemas(self):
        """测试 8: 获取所有 Schema"""
        print("\n" + "-"*40)
        print("[TEST] 测试获取所有 Schema")
        print("-"*40)

        schemas = self.connector.get_all_schemas()

        print(f"[INFO] 共 {len(schemas)} 张表的 Schema:")
        for table_name, schema in list(schemas.items())[:3]:
            print(f"  - {table_name}: {len(schema['columns'])} 列")

        self.assertIsInstance(schemas, dict)
        self.assertTrue(len(schemas) > 0)

        print(f"[PASS] 获取所有 Schema 成功")


def run_tests():
    """运行所有测试"""
    print("\n" + "="*60)
    print("DatabaseConnector 测试套件")
    print("="*60)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestDatabaseConnectorSQLite)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)

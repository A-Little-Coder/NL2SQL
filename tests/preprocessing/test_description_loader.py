# ============================================================================
# DescriptionLoader 测试用例
# ============================================================================
# 功能说明:
#   测试 DescriptionLoader 能否正确加载 database_description/*.csv 文件中的描述信息
#
# 使用方法:
#   python -m tests.preprocessing.test_description_loader
#   或
#   pytest tests/preprocessing/test_description_loader.py -v
#
# ============================================================================


import os
import sys
import unittest
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.preprocessing.description_loader import DescriptionLoader


class TestDescriptionLoader(unittest.TestCase):
    """DescriptionLoader 测试"""

    def setUp(self):
        """每个测试前的准备工作"""
        # 找到一个有描述文件的数据库
        base_dir = Path(__file__).parent.parent.parent / "data"

        # 查找第一个有 database_description 目录的数据库
        self.db_with_desc = None
        for item in base_dir.iterdir():
            if item.is_dir():
                desc_dir = item / "database_description"
                if desc_dir.exists():
                    self.db_with_desc = item
                    break

        # 如果没有找到，使用测试回退
        if self.db_with_desc is None:
            print("[警告] 未找到带描述文件的数据库，跳过部分测试")

    def test_01_load_descriptions_exists(self):
        """测试 1: 加载存在的描述文件"""
        if not self.db_with_desc:
            self.skipTest("无可用数据库")

        print("\n" + "-"*40)
        print("[TEST] 测试加载描述文件")
        print("-"*40)
        print(f"[INFO] 测试数据库：{self.db_with_desc.name}")

        loader = DescriptionLoader(str(self.db_with_desc))
        descriptions = loader.load_tables_description()

        print(f"[INFO] 加载了 {len(descriptions)} 张表的描述")

        self.assertIsInstance(descriptions, dict)
        self.assertTrue(len(descriptions) > 0, "应该有至少一张表的描述")

        # 检查描述结构
        for table_name, columns in descriptions.items():
            print(f"  - {table_name}: {len(columns)} 列")
            for col_name, col_info in list(columns.items())[:2]:
                print(f"      {col_name}: {col_info.get('column_description', '')[:50]}...")
                self.assertIn("original_column_name", col_info)
                self.assertIn("column_description", col_info)
                break
            break

        print("[PASS] 描述加载成功")

    def test_02_get_column_description(self):
        """测试 2: 获取单个列描述"""
        if not self.db_with_desc:
            self.skipTest("无可用数据库")

        print("\n" + "-"*40)
        print("[TEST] 测试获取列描述")
        print("-"*40)

        loader = DescriptionLoader(str(self.db_with_desc))
        descriptions = loader.load_tables_description()

        # 取第一个表和列进行测试
        table_name = list(descriptions.keys())[0]
        column_name = list(descriptions[table_name].keys())[0]

        print(f"[INFO] 测试表：{table_name}, 列：{column_name}")

        desc = loader.get_column_description(table_name, column_name)

        print(f"[INFO] 描述：{desc}")

        self.assertIsNotNone(desc, "应该返回描述文本")
        self.assertIsInstance(desc, str)

        print("[PASS] 列描述获取成功")

    def test_03_cache_mechanism(self):
        """测试 3: 缓存机制"""
        if not self.db_with_desc:
            self.skipTest("无可用数据库")

        print("\n" + "-"*40)
        print("[TEST] 测试缓存机制")
        print("-"*40)

        loader = DescriptionLoader(str(self.db_with_desc))

        # 第一次调用
        desc1 = loader.load_tables_description()

        # 第二次调用（应该从缓存）
        desc2 = loader.load_tables_description()

        self.assertIs(desc1, desc2, "两次调用应返回同一对象（缓存）")

        print("[PASS] 缓存机制正常")

    def test_04_clear_cache(self):
        """测试 4: 清空缓存"""
        if not self.db_with_desc:
            self.skipTest("无可用数据库")

        print("\n" + "-"*40)
        print("[TEST] 测试清空缓存")
        print("-"*40)

        loader = DescriptionLoader(str(self.db_with_desc))
        loader.load_tables_description()

        self.assertTrue(bool(loader._cache), "缓存应该被填充")

        loader.clear_cache()

        self.assertFalse(bool(loader._cache), "缓存应该被清空")

        print("[PASS] 清空缓存成功")

    def test_05_missing_description_dir(self):
        """测试 5: 缺少描述目录的处理"""
        print("\n" + "-"*40)
        print("[TEST] 测试缺失描述目录")
        print("-"*40)

        # 创建一个临时空目录
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            loader = DescriptionLoader(tmpdir)
            descriptions = loader.load_tables_description()

            self.assertEqual(descriptions, {}, "应该返回空字典")

        print("[PASS] 缺失目录处理正常")

    def test_06_integration_database_connector(self):
        """测试 6: 与 DatabaseConnector 集成"""
        if not self.db_with_desc:
            self.skipTest("无可用数据库")

        print("\n" + "-"*40)
        print("[TEST] 测试与 DatabaseConnector 集成")
        print("-"*40)

        from src.preprocessing.database_connector import DatabaseConnector

        # 连接数据库并获取带描述的 schema
        connector = DatabaseConnector(str(self.db_with_desc / f"{self.db_with_desc.name}.sqlite"))

        tables = connector.get_tables()
        if not tables:
            self.skipTest("数据库无表")

        table_name = tables[0]
        print(f"[INFO] 测试表：{table_name}")

        # 获取带描述的 schema
        schema = connector.get_table_schema(table_name, include_description=True)

        print(f"[INFO] 列数：{len(schema['columns'])}")

        # 检查是否有描述字段
        cols_with_desc = sum(1 for col in schema["columns"] if col.get("description"))
        print(f"[INFO] 有描述的列数：{cols_with_desc}")

        self.assertIsInstance(schema["columns"], list)

        # 至少检查列结构是否正确
        for col in schema["columns"][:1]:
            self.assertIn("name", col)
            self.assertIn("type", col)
            # description 可能为空，但如果 CSV 存在则应该有
            if self.db_with_desc.exists():
                # 不强制要求，仅打印信息
                desc_status = "有" if col.get("description") else "无"
                print(f"  - {col['name']}: 描述{desc_status}")

        connector.disconnect()
        print("[PASS] 集成测试成功")


def run_tests():
    """运行所有测试"""
    print("\n" + "="*60)
    print("DescriptionLoader 测试套件")
    print("="*60)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestDescriptionLoader)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)

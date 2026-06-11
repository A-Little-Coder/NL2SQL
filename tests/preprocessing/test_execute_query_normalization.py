# ============================================================================
# DatabaseConnector.execute_query() 返回 List[Dict] 测试（决策 51 修补）
# ============================================================================
# 验证：
# - execute_query SELECT 返回 List[Dict[str, Any]]，而非 sqlite3.Row 对象
# - 字典 key 为列名，可直接被 LLM prompt 展示
# - 空结果集返回空 list
# ============================================================================

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.preprocessing.database_connector import DatabaseConnector


class TestExecuteQueryRowNormalization(unittest.TestCase):
    """验证 SELECT 结果会被规范化为 List[Dict]，sqlite3.Row 不外泄"""

    @classmethod
    def setUpClass(cls):
        import sqlite3
        cls.tmp_dir = tempfile.mkdtemp()
        cls.db_path = str(Path(cls.tmp_dir) / "rows.db")
        conn = sqlite3.connect(cls.db_path)
        conn.execute("""
            CREATE TABLE schools (
                NCESSchool TEXT,
                School TEXT,
                Enrollment INTEGER
            )
        """)
        conn.executemany(
            "INSERT INTO schools VALUES (?, ?, ?)",
            [("001", "Alpha High", 1200), ("002", "Beta School", 850)],
        )
        conn.commit()
        conn.close()
        cls.connector = DatabaseConnector(cls.db_path, db_type="sqlite")

    @classmethod
    def tearDownClass(cls):
        import shutil
        cls.connector.disconnect()
        shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    def test_select_returns_list_of_dicts(self):
        """SELECT 返回的每一行应是 dict[列名→值]，不是 sqlite3.Row"""
        import sqlite3

        success, rows, _ = self.connector.execute_query(
            "SELECT NCESSchool, School, Enrollment FROM schools"
        )
        self.assertTrue(success)
        self.assertIsInstance(rows, list)
        self.assertEqual(len(rows), 2)

        # 关键：每行是 dict 而非 sqlite3.Row
        for row in rows:
            self.assertIsInstance(row, dict)
            self.assertNotIsInstance(row, sqlite3.Row)

        # 字典 key 为列名
        self.assertEqual(set(rows[0].keys()), {"NCESSchool", "School", "Enrollment"})
        self.assertEqual(rows[0]["School"], "Alpha High")
        self.assertEqual(rows[0]["Enrollment"], 1200)

    def test_str_row_is_readable(self):
        """str(row) 应输出可读的 dict 字符串，不再是 '<sqlite3.Row object ...>'"""
        success, rows, _ = self.connector.execute_query(
            "SELECT School FROM schools LIMIT 1"
        )
        self.assertTrue(success)
        s = str(rows[0])
        self.assertIn("School", s)
        self.assertIn("Alpha High", s)
        self.assertNotIn("sqlite3.Row object", s)

    def test_empty_result_returns_empty_list(self):
        """空结果集应返回空 list，不应进入转换分支报错"""
        success, rows, _ = self.connector.execute_query(
            "SELECT * FROM schools WHERE NCESSchool = 'nonexistent'"
        )
        self.assertTrue(success)
        self.assertEqual(rows, [])

    def test_aggregate_query_returns_dict(self):
        """聚合查询也应返回 dict，key 为聚合表达式或别名"""
        success, rows, _ = self.connector.execute_query(
            "SELECT COUNT(*) AS total FROM schools"
        )
        self.assertTrue(success)
        self.assertEqual(len(rows), 1)
        self.assertIsInstance(rows[0], dict)
        self.assertEqual(rows[0]["total"], 2)


if __name__ == "__main__":
    unittest.main()

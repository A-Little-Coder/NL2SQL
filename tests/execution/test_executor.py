# ============================================================================
# SQLExecutor / ErrorHandler / SQLFixLoop 测试用例
# ============================================================================
# 使用临时 SQLite 数据库进行测试


import os
import sys
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.execution.executor import (
    SQLExecutor, ErrorType, ErrorHandler,
    StructuredError, ExecutionResult, SQLFixLoop,
)
from src.preprocessing.database_connector import DatabaseConnector


class TestErrorHandler(unittest.TestCase):
    """ErrorHandler 测试"""

    def test_classify_syntax_error(self):
        result = ErrorHandler.classify_error("syntax error near 'FORM'")
        self.assertEqual(result, ErrorType.SYNTAX_ERROR)

    def test_classify_no_table(self):
        result = ErrorHandler.classify_error("no such table: users")
        self.assertEqual(result, ErrorType.SEMANTIC_ERROR)

    def test_classify_no_column(self):
        result = ErrorHandler.classify_error("no such column: amount")
        self.assertEqual(result, ErrorType.SEMANTIC_ERROR)

    def test_classify_timeout(self):
        result = ErrorHandler.classify_error("query timed out")
        self.assertEqual(result, ErrorType.TIMEOUT_ERROR)

    def test_classify_permission(self):
        result = ErrorHandler.classify_error("Access denied for user")
        self.assertEqual(result, ErrorType.PERMISSION_ERROR)

    def test_classify_unknown(self):
        result = ErrorHandler.classify_error("")
        self.assertEqual(result, ErrorType.UNKNOWN)

    def test_extract_table(self):
        table, col = ErrorHandler.extract_table_column("no such table: orders")
        self.assertEqual(table, "orders")
        self.assertIsNone(col)

    def test_extract_column(self):
        table, col = ErrorHandler.extract_table_column("no such column: orders.amount")
        self.assertEqual(table, "orders")
        self.assertEqual(col, "amount")

    def test_extract_column_no_table(self):
        table, col = ErrorHandler.extract_table_column("no such column: amount")
        self.assertEqual(col, "amount")

    def test_suggest_fix(self):
        err = StructuredError(error_type=ErrorType.SYNTAX_ERROR, original_message="x")
        fix = ErrorHandler.suggest_fix(err)
        self.assertIn("语法", fix)


class TestStructuredError(unittest.TestCase):
    def test_to_prompt_format(self):
        err = StructuredError(
            error_type=ErrorType.SEMANTIC_ERROR,
            original_message="no such column: x",
            column_name="x",
            suggested_fix="检查列名",
        )
        text = err.to_prompt_format()
        self.assertIn("semantic_error", text)
        self.assertIn("x", text)


class TestSQLExecutor(unittest.TestCase):
    """SQLExecutor 集成测试"""

    db_path = None
    connector = None
    executor = None

    @classmethod
    def setUpClass(cls):
        # 创建临时 SQLite 数据库
        cls.tmp_dir = tempfile.mkdtemp(prefix="nl2sql_exec_test_")
        cls.db_path = os.path.join(cls.tmp_dir, "test.db")

        conn = sqlite3.connect(cls.db_path)
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, age INTEGER)")
        conn.execute("INSERT INTO users VALUES (1, 'Alice', 30), (2, 'Bob', 25)")
        conn.commit()
        conn.close()

        cls.connector = DatabaseConnector(cls.db_path, db_type="sqlite")
        cls.executor = SQLExecutor(db_connector=cls.connector)

    @classmethod
    def tearDownClass(cls):
        import shutil
        if cls.connector:
            cls.connector.disconnect()
        if cls.tmp_dir and os.path.exists(cls.tmp_dir):
            shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    def test_no_connector(self):
        ex = SQLExecutor()
        result = ex.execute("SELECT 1")
        self.assertFalse(result.success)

    def test_execute_select_success(self):
        result = self.executor.execute("SELECT * FROM users")
        self.assertTrue(result.success)
        self.assertEqual(len(result.result_data), 2)
        self.assertIsNotNone(result.execution_time)

    def test_execute_no_such_table(self):
        result = self.executor.execute("SELECT * FROM nonexist_table")
        self.assertFalse(result.success)
        self.assertEqual(result.error.error_type, ErrorType.SEMANTIC_ERROR)
        self.assertEqual(result.error.table_name, "nonexist_table")

    def test_execute_no_such_column(self):
        result = self.executor.execute("SELECT nonexist_col FROM users")
        self.assertFalse(result.success)
        self.assertEqual(result.error.error_type, ErrorType.SEMANTIC_ERROR)

    def test_execute_syntax_error(self):
        result = self.executor.execute("SELCT * FROM users")
        self.assertFalse(result.success)
        self.assertEqual(result.error.error_type, ErrorType.SYNTAX_ERROR)

    def test_explain_success(self):
        result = self.executor.explain("SELECT * FROM users")
        self.assertTrue(result.success)
        self.assertIsNotNone(result.explain_plan)

    def test_explain_failure(self):
        result = self.executor.explain("SELECT * FROM nonexist")
        self.assertFalse(result.success)


class TestSQLFixLoop(unittest.TestCase):
    """错误修正循环测试"""

    @classmethod
    def setUpClass(cls):
        cls.tmp_dir = tempfile.mkdtemp(prefix="nl2sql_fix_test_")
        cls.db_path = os.path.join(cls.tmp_dir, "test.db")
        conn = sqlite3.connect(cls.db_path)
        conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, amount REAL)")
        conn.execute("INSERT INTO orders VALUES (1, 100.0), (2, 200.0)")
        conn.commit()
        conn.close()
        cls.connector = DatabaseConnector(cls.db_path, db_type="sqlite")

    @classmethod
    def tearDownClass(cls):
        import shutil
        cls.connector.disconnect()
        shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    def test_first_attempt_success_no_retry(self):
        executor = SQLExecutor(db_connector=self.connector)
        loop = SQLFixLoop(executor, llm_client=None, max_retries=2)
        # 决策 51：run() 返回 dict，首次执行直接成功
        ret = loop.run("SELECT * FROM orders", "查询所有订单")
        self.assertTrue(ret["result"].success)
        self.assertFalse(ret["fix_failed"])
        self.assertEqual(ret["fix_rounds_used"], 0)

    def test_fix_with_llm_mock(self):
        """LLM 提供修正后第二次成功"""
        import json
        mock_llm = MagicMock()
        mock_llm.stream.return_value = iter([(
            json.dumps({"sql": "SELECT * FROM orders", "reason": "修正表名"}), None
        )])
        executor = SQLExecutor(db_connector=self.connector)
        loop = SQLFixLoop(executor, llm_client=mock_llm, max_retries=2)

        # 首次错误，LLM 修正后成功（决策 51：run 返回 dict）
        ret = loop.run("SELECT * FROM order_typo", "查询所有订单")
        self.assertTrue(ret["result"].success)
        self.assertFalse(ret["fix_failed"])
        self.assertEqual(ret["fix_rounds_used"], 1)
        mock_llm.stream.assert_called()

    def test_fix_max_retries_reached(self):
        """超过最大重试次数返回最后一次的错误"""
        import json
        mock_llm = MagicMock()
        # 每次都返回错误的修正（迭代器需要重置）
        def fake_stream(*args, **kwargs):
            return iter([(
                json.dumps({"sql": "SELECT * FROM still_wrong", "reason": "尝试修正"}), None
            )])
        mock_llm.stream.side_effect = fake_stream

        executor = SQLExecutor(db_connector=self.connector)
        loop = SQLFixLoop(executor, llm_client=mock_llm, max_retries=2)
        ret = loop.run("SELECT * FROM wrong_table", "查询")
        self.assertFalse(ret["result"].success)
        self.assertTrue(ret["fix_failed"])
        # 应该尝试了 max_retries 次修正
        self.assertEqual(mock_llm.stream.call_count, 2)

    def test_no_llm_no_retry(self):
        """没有 LLM 时第一次失败就退出"""
        executor = SQLExecutor(db_connector=self.connector)
        loop = SQLFixLoop(executor, llm_client=None, max_retries=2)
        ret = loop.run("SELECT * FROM wrong_table", "查询")
        self.assertFalse(ret["result"].success)
        self.assertTrue(ret["fix_failed"])


if __name__ == "__main__":
    unittest.main()

# ============================================================================
# SmartFix（SQLFixLoop 决策 51 版）测试
# ============================================================================
# 覆盖：
# - 1 轮成功
# - 3 轮成功
# - 3 轮全失败 → fix_failed=True
# - 不可修错误 → 不调 LLM
# - fix_history 正确传递
# ============================================================================

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.execution.executor import (
    ErrorType, ExecutionResult, SQLFixLoop, StructuredError,
    UNFIXABLE_ERRORS, _format_fix_history,
)


def _err(msg, err_type=ErrorType.SEMANTIC_ERROR):
    return StructuredError(error_type=err_type, original_message=msg)


def _exec_success(sql="SELECT 1", result=None):
    return ExecutionResult(
        success=True, sql=sql, result_data=result or [(1,)], execution_time=0.01,
    )


def _exec_fail(sql, err_type=ErrorType.SEMANTIC_ERROR, msg="no such column"):
    return ExecutionResult(
        success=False, sql=sql, error=_err(msg, err_type), execution_time=0.005,
    )


class TestSmartFixLoop(unittest.TestCase):

    def test_first_round_success(self):
        """第 1 轮 LLM 修复后即成功"""
        executor = MagicMock()
        # 修复后执行成功
        executor.execute.return_value = _exec_success("SELECT fixed")

        llm = MagicMock()
        llm.chat_json.return_value = {"sql": "SELECT fixed", "reason": "fix"}

        loop = SQLFixLoop(executor=executor, llm_client=llm, max_retries=3)
        ret = loop.run(
            sql="SELECT bad",
            user_query="x",
            initial_error=_err("no such column"),
        )

        self.assertFalse(ret["fix_failed"])
        self.assertEqual(ret["fix_rounds_used"], 1)
        self.assertTrue(ret["result"].success)
        self.assertEqual(llm.chat_json.call_count, 1)

    def test_third_round_success(self):
        """前 2 轮失败，第 3 轮成功"""
        executor = MagicMock()
        # 3 次执行：失败、失败、成功
        executor.execute.side_effect = [
            _exec_fail("SELECT fixed1"),
            _exec_fail("SELECT fixed2"),
            _exec_success("SELECT fixed3"),
        ]
        llm = MagicMock()
        llm.chat_json.side_effect = [
            {"sql": "SELECT fixed1", "reason": "try1"},
            {"sql": "SELECT fixed2", "reason": "try2"},
            {"sql": "SELECT fixed3", "reason": "try3"},
        ]

        loop = SQLFixLoop(executor=executor, llm_client=llm, max_retries=3)
        ret = loop.run("SELECT bad", "x", initial_error=_err("no such column"))

        self.assertFalse(ret["fix_failed"])
        self.assertEqual(ret["fix_rounds_used"], 3)
        # 3 次 LLM 调用
        self.assertEqual(llm.chat_json.call_count, 3)

    def test_all_three_rounds_fail(self):
        """3 轮全部失败 → fix_failed=True"""
        executor = MagicMock()
        executor.execute.return_value = _exec_fail("SELECT bad")
        llm = MagicMock()
        llm.chat_json.side_effect = [
            {"sql": "SELECT v1", "reason": ""},
            {"sql": "SELECT v2", "reason": ""},
            {"sql": "SELECT v3", "reason": ""},
        ]

        loop = SQLFixLoop(executor=executor, llm_client=llm, max_retries=3)
        ret = loop.run("SELECT bad", "x", initial_error=_err("no such column"))

        self.assertTrue(ret["fix_failed"])
        self.assertEqual(ret["fix_rounds_used"], 3)
        self.assertEqual(llm.chat_json.call_count, 3)
        # 3 次执行（每轮修复后执行一次）
        self.assertEqual(executor.execute.call_count, 3)
        self.assertIsNotNone(ret["last_error"])

    def test_unfixable_error_skips_llm(self):
        """TIMEOUT/RUNTIME/PERMISSION 类错误不应调用 LLM"""
        executor = MagicMock()
        llm = MagicMock()
        loop = SQLFixLoop(executor=executor, llm_client=llm, max_retries=3)

        for err_type in UNFIXABLE_ERRORS:
            llm.reset_mock()
            executor.reset_mock()
            ret = loop.run(
                "SELECT bad", "x",
                initial_error=_err("timeout/runtime/perm error", err_type),
            )
            self.assertTrue(ret["fix_failed"], f"failed for {err_type}")
            self.assertEqual(ret["fix_rounds_used"], 0)
            self.assertEqual(llm.chat_json.call_count, 0,
                             f"LLM should not be called for {err_type}")

    def test_fix_history_passed_in_prompt(self):
        """第 2 轮 prompt 必须包含第 1 轮的 fix_history"""
        executor = MagicMock()
        executor.execute.side_effect = [
            _exec_fail("SELECT fixed1", msg="error round1"),
            _exec_success("SELECT fixed2"),
        ]
        llm = MagicMock()
        llm.chat_json.side_effect = [
            {"sql": "SELECT fixed1", "reason": ""},
            {"sql": "SELECT fixed2", "reason": ""},
        ]

        loop = SQLFixLoop(executor=executor, llm_client=llm, max_retries=3)
        loop.run("SELECT bad", "x", initial_error=_err("no such column"))

        # 第 2 次 LLM 调用的 prompt 应包含 fix_history
        second_call_messages = llm.chat_json.call_args_list[1][0][0]
        user_msg = second_call_messages[1]["content"]
        self.assertIn("历次修复尝试", user_msg)
        self.assertIn("第 1 轮", user_msg)
        self.assertIn("SELECT fixed1", user_msg)
        self.assertIn("error round1", user_msg)

    def test_llm_returns_same_sql_breaks_early(self):
        """LLM 返回相同 SQL 时提前结束（避免死循环）"""
        executor = MagicMock()
        executor.execute.return_value = _exec_fail("SELECT bad")
        llm = MagicMock()
        # 第 1 次返回相同的 SELECT bad
        llm.chat_json.return_value = {"sql": "SELECT bad", "reason": ""}

        loop = SQLFixLoop(executor=executor, llm_client=llm, max_retries=3)
        ret = loop.run("SELECT bad", "x", initial_error=_err("no such column"))

        self.assertTrue(ret["fix_failed"])
        # LLM 只被调用 1 次（提前结束）
        self.assertEqual(llm.chat_json.call_count, 1)

    def test_format_fix_history_empty(self):
        """空 history 应返回空字符串"""
        self.assertEqual(_format_fix_history([]), "")
        self.assertEqual(_format_fix_history(None or []), "")

    def test_format_fix_history_content(self):
        """history 应包含轮次、SQL、错误"""
        s = _format_fix_history([
            {"round": 1, "sql": "SELECT a", "error": "err1"},
            {"round": 2, "sql": "SELECT b", "error": "err2"},
        ])
        self.assertIn("第 1 轮", s)
        self.assertIn("SELECT a", s)
        self.assertIn("err1", s)
        self.assertIn("第 2 轮", s)
        self.assertIn("SELECT b", s)


if __name__ == "__main__":
    unittest.main()

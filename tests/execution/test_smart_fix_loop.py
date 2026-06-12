# ============================================================================
# SmartFix（SQLFixLoop 决策 51 版）测试 — 适配新 invoke/stream API
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
from unittest.mock import MagicMock

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


def _stream_returning(sql: str, reason: str = ""):
    """构造一个 mock stream 迭代器，yield JSON 文本"""
    import json
    body = json.dumps({"sql": sql, "reason": reason})
    return iter([(body, None)])


class TestSmartFixLoop(unittest.TestCase):

    def test_first_round_success(self):
        """第 1 轮 LLM 修复后即成功"""
        executor = MagicMock()
        executor.execute.return_value = _exec_success("SELECT fixed")

        llm = MagicMock()
        llm.stream.return_value = _stream_returning("SELECT fixed", "fix")

        loop = SQLFixLoop(executor=executor, llm_client=llm, max_retries=3)
        ret = loop.run(
            sql="SELECT bad",
            user_query="x",
            initial_error=_err("no such column"),
        )

        self.assertFalse(ret["fix_failed"])
        self.assertEqual(ret["fix_rounds_used"], 1)
        self.assertTrue(ret["result"].success)
        self.assertEqual(llm.stream.call_count, 1)

    def test_third_round_success(self):
        """前 2 轮失败，第 3 轮成功"""
        executor = MagicMock()
        executor.execute.side_effect = [
            _exec_fail("SELECT fixed1"),
            _exec_fail("SELECT fixed2"),
            _exec_success("SELECT fixed3"),
        ]
        llm = MagicMock()
        llm.stream.side_effect = [
            _stream_returning("SELECT fixed1", "try1"),
            _stream_returning("SELECT fixed2", "try2"),
            _stream_returning("SELECT fixed3", "try3"),
        ]

        loop = SQLFixLoop(executor=executor, llm_client=llm, max_retries=3)
        ret = loop.run("SELECT bad", "x", initial_error=_err("no such column"))

        self.assertFalse(ret["fix_failed"])
        self.assertEqual(ret["fix_rounds_used"], 3)
        self.assertEqual(llm.stream.call_count, 3)

    def test_all_three_rounds_fail(self):
        """3 轮全部失败 → fix_failed=True"""
        executor = MagicMock()
        executor.execute.return_value = _exec_fail("SELECT bad")
        llm = MagicMock()
        llm.stream.side_effect = [
            _stream_returning("SELECT v1"),
            _stream_returning("SELECT v2"),
            _stream_returning("SELECT v3"),
        ]

        loop = SQLFixLoop(executor=executor, llm_client=llm, max_retries=3)
        ret = loop.run("SELECT bad", "x", initial_error=_err("no such column"))

        self.assertTrue(ret["fix_failed"])
        self.assertEqual(ret["fix_rounds_used"], 3)
        self.assertEqual(llm.stream.call_count, 3)
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
            self.assertEqual(llm.stream.call_count, 0,
                             f"LLM should not be called for {err_type}")

    def test_fix_history_passed_in_prompt(self):
        """第 2 轮 prompt 必须包含第 1 轮的 fix_history"""
        executor = MagicMock()
        executor.execute.side_effect = [
            _exec_fail("SELECT fixed1", msg="error round1"),
            _exec_success("SELECT fixed2"),
        ]
        llm = MagicMock()
        llm.stream.side_effect = [
            _stream_returning("SELECT fixed1"),
            _stream_returning("SELECT fixed2"),
        ]

        loop = SQLFixLoop(executor=executor, llm_client=llm, max_retries=3)
        loop.run("SELECT bad", "x", initial_error=_err("no such column"))

        # 第 2 次 LLM 调用的 messages 应包含 fix_history
        second_call_args = llm.stream.call_args_list[1]
        messages = second_call_args.args[0]
        # messages 是 List[BaseMessage]，user 消息在 index 1
        user_msg = messages[1].content
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
        llm.stream.return_value = _stream_returning("SELECT bad")

        loop = SQLFixLoop(executor=executor, llm_client=llm, max_retries=3)
        ret = loop.run("SELECT bad", "x", initial_error=_err("no such column"))

        self.assertTrue(ret["fix_failed"])
        # LLM 只被调用 1 次（提前结束）
        self.assertEqual(llm.stream.call_count, 1)

    def test_format_fix_history_empty(self):
        """空 history 应返回空字符串"""
        self.assertEqual(_format_fix_history([]), "")
        self.assertEqual(_format_fix_history(None or []), "")

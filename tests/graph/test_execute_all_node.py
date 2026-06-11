# ============================================================================
# ExecuteAll 节点测试（决策 51）
# ============================================================================
# 验证：
# - 5 候选都执行
# - 不触发任何 LLM 修复调用
# - status / result / execution_time 回填正确
# ============================================================================

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.execution.executor import ExecutionResult, StructuredError, ErrorType
from src.graph.main_graph import make_execution_node
from src.sql_generation.sql_generator import SQLCandidate, SQLStatus


def _make_state(candidates, schema=None):
    return {
        "user_query": "查询用户表",
        "sql_candidates": candidates,
        "selected_schema": schema or [],
        "trace_log": [],
    }


class TestExecuteAllNode(unittest.TestCase):
    """ExecuteAll 节点行为测试"""

    def setUp(self):
        # mock fix_loop：仅需要 executor 字段
        self.executor = MagicMock()
        self.llm_client = MagicMock()
        self.fix_loop = MagicMock()
        self.fix_loop.executor = self.executor
        self.fix_loop.llm_client = self.llm_client

    def test_all_candidates_executed(self):
        """5 个候选都应被执行"""
        self.executor.execute.return_value = ExecutionResult(
            success=True, sql="SELECT 1",
            result_data=[(1,)], execution_time=0.01,
        )
        candidates = [
            SQLCandidate(id=f"c{i}", sql=f"SELECT {i}")
            for i in range(5)
        ]
        node = make_execution_node(self.fix_loop)
        node(_make_state(candidates))

        # 5 次执行
        self.assertEqual(self.executor.execute.call_count, 5)

    def test_no_llm_fix_calls(self):
        """ExecuteAll 不应触发任何 LLM 调用（即使全部失败）"""
        self.executor.execute.return_value = ExecutionResult(
            success=False, sql="SELECT bad",
            error=StructuredError(
                error_type=ErrorType.SYNTAX_ERROR,
                original_message="syntax error",
            ),
            execution_time=0.005,
        )
        candidates = [SQLCandidate(id=f"c{i}", sql="SELECT bad") for i in range(3)]
        node = make_execution_node(self.fix_loop)
        node(_make_state(candidates))

        # 关键：LLM 客户端任何方法都不应被调用
        self.assertEqual(self.llm_client.chat_json.call_count, 0)
        self.assertEqual(self.llm_client.chat.call_count, 0)
        # 也不应调用 build_graph（无 fix 子图）
        self.assertEqual(self.fix_loop.build_graph.call_count, 0)

    def test_success_fields_filled(self):
        """成功时 status/result/execution_time 应被回填"""
        self.executor.execute.return_value = ExecutionResult(
            success=True, sql="SELECT 1",
            result_data=[(1, "a"), (2, "b")],
            execution_time=0.02,
        )
        cand = SQLCandidate(id="c0", sql="SELECT 1")
        node = make_execution_node(self.fix_loop)
        node(_make_state([cand]))

        self.assertEqual(cand.status, SQLStatus.SUCCESS)
        self.assertEqual(cand.result, [(1, "a"), (2, "b")])
        self.assertEqual(cand.execution_time, 0.02)
        self.assertIsNone(cand.error_message)
        self.assertIsNone(cand.structured_error)

    def test_failure_fields_filled(self):
        """失败时 status=FAILED，error_message 和 structured_error 应被保留"""
        err = StructuredError(
            error_type=ErrorType.SEMANTIC_ERROR,
            original_message="no such column: foo",
        )
        self.executor.execute.return_value = ExecutionResult(
            success=False, sql="SELECT foo",
            error=err,
            execution_time=0.003,
        )
        cand = SQLCandidate(id="c0", sql="SELECT foo")
        node = make_execution_node(self.fix_loop)
        node(_make_state([cand]))

        self.assertEqual(cand.status, SQLStatus.FAILED)
        self.assertEqual(cand.error_message, "no such column: foo")
        # 结构化错误对象保留供 SmartFix 使用
        self.assertIs(cand.structured_error, err)

    def test_empty_candidates_returns_error(self):
        """无候选时应返回 error"""
        node = make_execution_node(self.fix_loop)
        result = node({"sql_candidates": [], "selected_schema": []})
        self.assertIn("error", result)

    def test_cache_hit_path(self):
        """cache_hit=True 时应从 cached_sql 构造单候选"""
        self.executor.execute.return_value = ExecutionResult(
            success=True, sql="SELECT * FROM cached",
            result_data=[(1,)], execution_time=0.001,
        )
        node = make_execution_node(self.fix_loop)
        result = node({
            "cache_hit": True,
            "cached_sql": "SELECT * FROM cached",
            "selected_schema": [],
            "trace_log": [],
        })
        # 应该只执行一次
        self.assertEqual(self.executor.execute.call_count, 1)
        # 候选已被生成并回填
        cands = result["sql_candidates"]
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0].id, "cache_hit")


if __name__ == "__main__":
    unittest.main()

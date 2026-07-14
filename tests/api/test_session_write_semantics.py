# ============================================================================
# 会话历史写入语义测试（relax-session-write-gate 反转）
# ============================================================================
# 验证 _should_write_session_turn：
#   - 所有非反问挂起的轮次均写入（包括失败/拒答/早退轮次）
#   - 反问挂起（__interrupted__）→ 不写（行为不变）
#
# 注意：reuse_eligible 标记在 turn_data 构造中计算，不在 _should_write_session_turn 中。
# _should_write_session_turn 只决定"是否写入"，不决定"是否可复用"。
#
# 运行: pytest tests/api/test_session_write_semantics.py -v
# ============================================================================

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.api.routes.query import _should_write_session_turn


class TestShouldWriteSessionTurn(unittest.TestCase):
    """会话写入拦截条件（变更后：仅反问挂起拦截）"""

    def test_rejected_turn_written(self):
        """拒答请求（rejection_reason 非空、无 final_sql）写入会话（改写模块需要可见）"""
        accumulated = {
            "rejection_reason": "检测到写操作意图",
            "final_sql": "",
            "error": "拒答: ...",
        }
        self.assertTrue(_should_write_session_turn(accumulated))

    def test_failed_no_sql_written(self):
        """无 SQL 失败请求（fail-fast 早退）写入会话"""
        accumulated = {
            "final_sql": "",
            "decision_path": "",
            "error": "不可回答: ...",
        }
        self.assertTrue(_should_write_session_turn(accumulated))

    def test_smartfix_failed_written(self):
        """SmartFix 全失败（有候选 SQL 且 fix_failed=True、final_sql 非空）写入会话"""
        accumulated = {
            "final_sql": "SELECT * FROM non_existent_table",
            "fix_failed": True,
            "decision_path": "E",
            "last_error": "SmartFix 3 轮失败: 表不存在",
        }
        self.assertTrue(_should_write_session_turn(accumulated))

    def test_fixfailed_nonempty_sql_written(self):
        """SmartFix 失败但 final_sql 非空写入会话（盲区覆盖）"""
        accumulated = {
            "final_sql": "SELECT invalid_column FROM schools",
            "fix_failed": True,
            "decision_path": "E",
            "last_error": "SmartFix 失败: 列不存在",
        }
        self.assertTrue(_should_write_session_turn(accumulated))

    def test_successful_turn_written(self):
        """成功请求（final_sql 非空）写入会话"""
        accumulated = {
            "final_sql": "SELECT AVG(score) FROM schools",
            "final_result": [{"avg": 85.5}],
            "decision_path": "A",
        }
        self.assertTrue(_should_write_session_turn(accumulated))

    def test_cache_hit_success_written(self):
        """cache 命中且产出 SQL 写入会话"""
        accumulated = {
            "final_sql": "SELECT 1",
            "cache_hit": True,
            "decision_path": "A",
        }
        self.assertTrue(_should_write_session_turn(accumulated))

    def test_interrupted_not_written(self):
        """反问挂起不入会话（行为不变）"""
        accumulated = {
            "__interrupted__": True,
            "final_sql": "SELECT 1",  # 即使有 SQL，挂起也不写
        }
        self.assertFalse(_should_write_session_turn(accumulated))

    def test_empty_accumulated_written(self):
        """空 accumulated（兜底）写入会话（reuse_eligible=False 在 turn_data 构造处计算）"""
        self.assertTrue(_should_write_session_turn({}))


if __name__ == "__main__":
    unittest.main()
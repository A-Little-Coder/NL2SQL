# ============================================================================
# 会话历史写入语义测试（fix-keyword-history-pollution）
# ============================================================================
# 验证 _should_write_session_turn：
#   - 拒答（rejection_reason 非空、无 final_sql）→ 不写
#   - 无 SQL 失败（fail-fast / SmartFix 失败）→ 不写
#   - 成功（final_sql 非空）→ 写
#   - 反问挂起（__interrupted__）→ 不写（行为不变）
#
# 运行: pytest tests/api/test_session_write_semantics.py -v
# ============================================================================

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.api.routes.query import _should_write_session_turn


class TestShouldWriteSessionTurn(unittest.TestCase):
    """会话写入拦截条件（任务 3.2-3.5）"""

    def test_rejected_turn_not_written(self):
        """拒答请求（rejection_reason 非空、无 final_sql）不入会话"""
        accumulated = {
            "rejection_reason": "检测到写操作意图",
            "final_sql": "",
            "error": "拒答: ...",
        }
        self.assertFalse(_should_write_session_turn(accumulated))

    def test_failed_no_sql_not_written(self):
        """无 SQL 失败请求（fail-fast 早退）不入会话"""
        accumulated = {
            "final_sql": "",
            "decision_path": "",
            "error": "不可回答: ...",
        }
        self.assertFalse(_should_write_session_turn(accumulated))

    def test_smartfix_failed_not_written(self):
        """SmartFix 全失败（有候选 SQL 且 fix_failed=True、final_sql 非空）不入会话"""
        accumulated = {
            "final_sql": "SELECT * FROM non_existent_table",
            "fix_failed": True,
            "decision_path": "E",
            "last_error": "SmartFix 3 轮失败: 表不存在",
        }
        self.assertFalse(_should_write_session_turn(accumulated))

    def test_fixfailed_nonempty_sql_not_written(self):
        """SmartFix 失败但 final_sql 非空（盲区覆盖）不入会话"""
        accumulated = {
            "final_sql": "SELECT invalid_column FROM schools",
            "fix_failed": True,
            "decision_path": "E",
            "last_error": "SmartFix 失败: 列不存在",
        }
        self.assertFalse(_should_write_session_turn(accumulated))

    def test_successful_turn_written(self):
        """成功请求（final_sql 非空）入会话"""
        accumulated = {
            "final_sql": "SELECT AVG(score) FROM schools",
            "final_result": [{"avg": 85.5}],
            "decision_path": "A",
        }
        self.assertTrue(_should_write_session_turn(accumulated))

    def test_cache_hit_success_written(self):
        """cache 命中且产出 SQL 仍入会话"""
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

    def test_empty_accumulated_not_written(self):
        """空 accumulated（兜底）不入会话"""
        self.assertFalse(_should_write_session_turn({}))


if __name__ == "__main__":
    unittest.main()

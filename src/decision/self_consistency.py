# ============================================================================
# Self-Consistency 决策模块
# ============================================================================
# 功能说明:
#   1. 两段式评分（R1 数据视角 + R2 SQL 视角）选出最优 SQL 候选
#   2. SmartFix 修复低分候选（决策 51）
#   3. 结果哈希计算（供外部使用）
# ============================================================================


import json
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
from loguru import logger

from src.sql_generation.sql_generator import SQLCandidate, SQLStatus
from src.decision.prompts import (
    SCORE_BY_DATA_PROMPT,
    SCORE_BY_SQL_PROMPT,
)
from utils.llm_client import parse_json, stream_with_sse


# ============================================================================
# 决策 51：评分常量
# ============================================================================

SCORE_DATA_TOPK = 20      # R1 评分展示的最大行数
SCORE_DATA_CELL_MAX = 20  # R1 评分单元格内容截断字符数

# R1 / R2 评分 prompt 已迁移至 src/decision/prompts.py


@dataclass
class DecisionResult:
    """决策结果"""
    selected_sql: str = None
    selected_result: Any = None
    execution_time: float = None
    decision_reason: str = None
    voting_summary: Dict[str, Any] = None

    # ===== 决策 51：两段式评分 + 单候选修复（扩展字段，向后兼容） =====
    candidate_scores_r1: List[Dict[str, Any]] = field(default_factory=list)
    candidate_scores_r2: Optional[List[Dict[str, Any]]] = None
    selected_candidate_id: Optional[str] = None
    fix_failed: bool = False
    fix_rounds_used: int = 0
    last_error: Optional[str] = None
    decision_path: str = ""   # "A"/"B"/"C"/"D"/"E"/"F"/"G"/"H"


class SelfConsistencyDecision:
    """
    Self-Consistency 决策器

    决策逻辑:
    1. 多数结果一致 → 选择执行时间最短的 SQL
    2. 所有结果不同 → 调用 LLM 进行最终决策
    3. 全部失败 → 返回错误
    4. 选定 SQL 后 → 调用 ResultVerifier 验证结果可信度（决策 24）

    Attributes:
        llm_client: LLM 客户端
        num_candidates: 候选数量（默认 5）
        result_verifier: 结果可信度验证器（决策 24）
    """

    def __init__(self, llm_client=None, num_candidates: int = 5,
                 result_verifier=None, fix_loop=None):
        """
        Args:
            llm_client: LLM 客户端
            num_candidates: 候选数量（默认 5）
            result_verifier: 结果可信度验证器（决策 24）
            fix_loop: SQLFixLoop 实例（决策 51；为 None 时 SmartFix 节点会用 llm_client 兜底）
        """
        self.llm_client = llm_client
        self.num_candidates = num_candidates
        self.result_verifier = result_verifier
        self.fix_loop = fix_loop
        # 暴露 executor 给 Decision 子图作为 fix_loop 缺失时的兜底
        self.executor = fix_loop.executor if fix_loop is not None else None

    # ------------------------------------------------------------------
    # 决策 51：两段式评分 + SmartFix 相关方法
    # ------------------------------------------------------------------

    @staticmethod
    def _truncate_cell(value: Any, max_len: int = SCORE_DATA_CELL_MAX) -> str:
        """单元格内容截断到 max_len 字符（决策 51）"""
        s = "" if value is None else str(value)
        if len(s) <= max_len:
            return s
        return s[:max_len] + "..."

    @classmethod
    def _format_candidate_data_preview(
        cls,
        cand: SQLCandidate,
        topk: int = SCORE_DATA_TOPK,
    ) -> str:
        """构造单候选的数据预览文本（列名 + topk 行 + 截断 cell + 元信息）

        Args:
            cand: 已执行成功的候选
            topk: 最大行数

        Returns:
            str: 给 LLM 的数据预览文本
        """
        result = cand.result if cand.result is not None else []
        if not isinstance(result, list):
            result = [result]

        row_count = len(result)
        preview_rows = result[:topk]

        # 尝试推断列名（result 元素是 dict 时取 keys，是 tuple/list 时用 col_0/1/...）
        columns: List[str] = []
        if preview_rows:
            first = preview_rows[0]
            if isinstance(first, dict):
                columns = list(first.keys())
            elif isinstance(first, (list, tuple)):
                columns = [f"col_{i}" for i in range(len(first))]

        # 截断每个 cell
        truncated_rows: List[List[str]] = []
        for row in preview_rows:
            if isinstance(row, dict):
                truncated_rows.append([cls._truncate_cell(row.get(c)) for c in columns])
            elif isinstance(row, (list, tuple)):
                truncated_rows.append([cls._truncate_cell(c) for c in row])
            else:
                truncated_rows.append([cls._truncate_cell(row)])

        exec_time = f"{cand.execution_time:.3f}s" if cand.execution_time is not None else "N/A"

        lines = [
            f"候选 ID: {cand.id}",
            f"执行时间: {exec_time}",
            f"返回行数: {row_count}（仅展示前 {min(topk, row_count)} 行）",
            f"列名: {columns if columns else '(无列信息)'}",
            f"数据预览:",
        ]
        if truncated_rows:
            for i, row in enumerate(truncated_rows, 1):
                lines.append(f"  [{i}] {row}")
        else:
            lines.append("  (无数据)")
        return "\n".join(lines)

    def score_by_data(
        self,
        candidates: List[SQLCandidate],
        user_query: str,
    ) -> List[Dict[str, Any]]:
        """R1 数据视角评分（决策 51）

        - 仅对执行成功的候选评分
        - prompt 不包含 SQL 代码（强制 LLM 仅基于数据评分）
        - 结果数据展示 top-20 行，cell 截断 20 字符
        - prompt 明示节选

        Args:
            candidates: 已执行成功的候选列表
            user_query: 用户原始查询

        Returns:
            List[Dict]: [{candidate_id, score, reason}]
                       LLM 失败时返回空 list（路由器需兼容）
        """
        # 过滤成功候选
        success_cands = [c for c in candidates if c.status == SQLStatus.SUCCESS]
        if not success_cands:
            return []

        # 构造数据预览
        candidates_text = "\n\n".join(
            self._format_candidate_data_preview(c, SCORE_DATA_TOPK)
            for c in success_cands
        )

        prompt_messages = SCORE_BY_DATA_PROMPT.format_messages(
            topk=SCORE_DATA_TOPK,
            user_query=user_query,
            candidates_text=candidates_text,
        )

        if not self.llm_client:
            logger.warning("[R1] llm_client 未设置，跳过评分（所有候选记 0 分）")
            return [
                {"candidate_id": c.id, "score": 0, "reason": "LLM 未启用"}
                for c in success_cands
            ]

        try:
            raw = stream_with_sse(self.llm_client.stream(prompt_messages, as_json=True, temperature=0.0, run_name="decision-r1"))
            result = parse_json(raw)
            scores = result.get("scores", [])
            # 校验：每条必须有 candidate_id 和 score
            valid_scores = []
            for s in scores:
                cid = s.get("candidate_id")
                score = s.get("score")
                if cid is None or score is None:
                    continue
                valid_scores.append({
                    "candidate_id": cid,
                    "score": int(score) if isinstance(score, (int, float, str)) else 0,
                    "reason": s.get("reason", ""),
                })

            # 业务事件推送
            try:
                from src.api.streaming import emit_safe
                emit_safe("score_r1", {"scores": valid_scores})
            except Exception:
                pass

            return valid_scores

        except Exception as e:
            logger.error(f"[R1] 数据视角评分失败: {e}")
            return []

    def score_by_sql(
        self,
        candidates: List[SQLCandidate],
        user_query: str,
        r1_scores: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """R2 SQL 视角评分（决策 51）

        仅在 R1 出现并列最高分=5 时触发。

        Args:
            candidates: 待评分候选（已是 R1 并列最高分组）
            user_query: 用户原始查询
            r1_scores: R1 评分结果（用于在 prompt 中传递上下文）

        Returns:
            List[Dict]: [{candidate_id, score, reason}]
        """
        if not candidates:
            return []

        # 构造 R1 评价 lookup
        r1_lookup = {s["candidate_id"]: s for s in r1_scores}

        # 构造候选文本（SQL + 执行时间 + R1 评价）
        parts = []
        for c in candidates:
            r1 = r1_lookup.get(c.id, {})
            exec_time = f"{c.execution_time:.3f}s" if c.execution_time is not None else "N/A"
            parts.append(
                f"候选 ID: {c.id}\n"
                f"SQL:\n{c.sql}\n"
                f"执行时间: {exec_time}\n"
                f"第一轮(数据视角)评分: {r1.get('score', 'N/A')}/5\n"
                f"第一轮评价: {r1.get('reason', '')}"
            )
        candidates_text = "\n\n".join(parts)

        prompt_messages = SCORE_BY_SQL_PROMPT.format_messages(
            user_query=user_query,
            candidates_text=candidates_text,
        )

        if not self.llm_client:
            logger.warning("[R2] llm_client 未设置，跳过评分")
            return [
                {"candidate_id": c.id, "score": 0, "reason": "LLM 未启用"}
                for c in candidates
            ]

        try:
            raw = stream_with_sse(self.llm_client.stream(prompt_messages, as_json=True, temperature=0.0, run_name="decision-r2"))
            result = parse_json(raw)
            scores = result.get("scores", [])
            valid_scores = []
            for s in scores:
                cid = s.get("candidate_id")
                score = s.get("score")
                if cid is None or score is None:
                    continue
                valid_scores.append({
                    "candidate_id": cid,
                    "score": int(score) if isinstance(score, (int, float, str)) else 0,
                    "reason": s.get("reason", ""),
                })

            # 业务事件推送
            try:
                from src.api.streaming import emit_safe
                emit_safe("score_r2", {
                    "scores": valid_scores,
                    "triggered_by": "r1_tie_at_5",
                })
            except Exception:
                pass

            return valid_scores

        except Exception as e:
            logger.error(f"[R2] SQL 视角评分失败: {e}")
            return []

    @staticmethod
    def _pick_from_scores(
        candidates: List[SQLCandidate],
        scores: List[Dict[str, Any]],
    ) -> Tuple[Optional[str], bool, int]:
        """从评分结果中挑出最高分候选（决策 51）

        Args:
            candidates: 候选列表（提供 execution_time）
            scores: 评分列表 [{candidate_id, score, reason}]

        Returns:
            (best_id, is_tied, top_score)
            - best_id: 最高分候选 ID（并列时按 execution_time 选最快；评分缺失则取候选列表首位）
            - is_tied: 是否并列（>1 个候选并列最高分）
            - top_score: 最高分值
        """
        if not scores:
            # 兜底：取第一个候选
            if candidates:
                return candidates[0].id, False, 0
            return None, False, 0

        max_score = max(s.get("score", 0) for s in scores)
        top_ids = [s["candidate_id"] for s in scores if s.get("score", 0) == max_score]

        if len(top_ids) == 1:
            return top_ids[0], False, max_score

        # 并列：按 execution_time 选最快（稳定排序：相同时间保留候选列表中靠前的）
        id_to_idx = {c.id: i for i, c in enumerate(candidates)}
        id_to_time = {c.id: (c.execution_time or float("inf")) for c in candidates}
        sorted_ids = sorted(top_ids, key=lambda cid: (id_to_time.get(cid, float("inf")), id_to_idx.get(cid, 99999)))
        return sorted_ids[0], True, max_score

    @staticmethod
    def pick_lightest_failures(candidates: List[SQLCandidate]) -> List[SQLCandidate]:
        """全失败分支：按错误等级取最轻一级的所有候选（决策 51）

        - 按 ERROR_SEVERITY 排序（轻→重）
        - 取最轻一级的全部候选（可能多个）
        - 若最轻级别全是 UNFIXABLE_ERRORS → 返回空 list（路由器据此判定路径 H）

        Args:
            candidates: 全部失败的候选列表

        Returns:
            List[SQLCandidate]: 最轻一级的可修候选（按原顺序）
        """
        from src.execution.executor import ERROR_SEVERITY, UNFIXABLE_ERRORS, ErrorType

        if not candidates:
            return []

        def severity(c: SQLCandidate) -> int:
            err = getattr(c, "structured_error", None)
            if err is None or not hasattr(err, "error_type"):
                return ERROR_SEVERITY.get(ErrorType.UNKNOWN, 99)
            return ERROR_SEVERITY.get(err.error_type, 99)

        min_sev = min(severity(c) for c in candidates)
        lightest = [c for c in candidates if severity(c) == min_sev]

        # 若最轻级别属于不可修类型 → 返回空
        if lightest:
            first_err = getattr(lightest[0], "structured_error", None)
            if first_err is not None and first_err.error_type in UNFIXABLE_ERRORS:
                return []

        return lightest

    # ------------------------------------------------------------------
    # LangGraph 子图接口（§18.7 / §18.8）
    # ------------------------------------------------------------------
    def build_graph(self):
        """
        返回 Decision Agent 的已编译 LangGraph 子图

        子图节点：filter → score_r1 → [路由]
        子图输入字段：candidates (List[SQLCandidate]), user_query
        子图输出字段：final_decision (DecisionResult)
        """
        from src.decision.decision_graph import build_decision_graph
        return build_decision_graph(self)

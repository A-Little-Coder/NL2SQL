# ============================================================================
# Self-Consistency 决策模块
# ============================================================================
# 功能说明:
#   1. 检测多个 SQL 候选的执行结果一致性
#   2. 实现投票决策逻辑：多数一致选最快，全不同调用 LLM
#   3. 集成 LLM 最终决策功能
# ============================================================================


import hashlib
import json
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
from collections import Counter
from loguru import logger

from src.sql_generation.sql_generator import SQLCandidate, SQLStatus
from src.decision.prompts import (
    LLM_FINAL_DECISION_PROMPT,
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

    def compute_result_hash(self, result: Any) -> str:
        """
        计算执行结果的哈希值（用于比较一致性）

        将结果规范化为字符串后取 MD5。
        对于数值型结果，会进行精度归一化（保留 4 位小数）。

        Args:
            result: 执行结果

        Returns:
            str: 结果哈希字符串
        """
        if result is None:
            return "__none__"

        try:
            # 将结果转换为可排序、可比较的规范形式
            if isinstance(result, list):
                # 每行转为 tuple，排序后取 hash
                normalized = []
                for row in result:
                    if isinstance(row, (list, tuple)):
                        normalized_row = []
                        for cell in row:
                            if isinstance(cell, float):
                                normalized_row.append(round(cell, 4))
                            else:
                                normalized_row.append(cell)
                        normalized.append(tuple(normalized_row))
                    else:
                        normalized.append(row)
                normalized.sort()
                canonical = json.dumps(normalized, sort_keys=True, default=str, ensure_ascii=False)
            else:
                canonical = json.dumps(result, sort_keys=True, default=str, ensure_ascii=False)

            return hashlib.md5(canonical.encode()).hexdigest()
        except Exception:
            # 退回到字符串 hash
            return hashlib.md5(str(result).encode()).hexdigest()

    def group_by_result(self, candidates: List[SQLCandidate]) -> Dict[str, List[SQLCandidate]]:
        """
        按执行结果对候选进行分组

        Args:
            candidates: SQLCandidate 列表（需要有执行结果）

        Returns:
            Dict[str, List]: 结果哈希到候选列表的映射
        """
        groups: Dict[str, List[SQLCandidate]] = {}

        for cand in candidates:
            if cand.status == SQLStatus.SUCCESS and cand.result is not None:
                h = self.compute_result_hash(cand.result)
            else:
                # 失败的候选统一归入 "__failed__" 组
                h = "__failed__"

            groups.setdefault(h, []).append(cand)

        return groups

    def find_majority_group(self, groups: Dict[str, List[Any]],
                            threshold: float = 0.5) -> tuple:
        """
        查找多数组（超过阈值的组）

        只考虑成功执行的组（排除 __failed__）。

        Args:
            groups: 结果分组
            threshold: 多数阈值（默认 0.5，即 50%）

        Returns:
            tuple: (是否有多数组, 多数组的 key, 该组成员)
        """
        # 计算成功候选总数
        successful_groups = {k: v for k, v in groups.items() if k != "__failed__"}
        total_successful = sum(len(v) for v in successful_groups.values())

        if total_successful == 0:
            return False, None, []

        for key, group in successful_groups.items():
            if len(group) / total_successful > threshold:
                return True, key, group

        return False, None, []

    def select_fastest_from_group(self, group: List[SQLCandidate]) -> SQLCandidate:
        """
        从一组中选择执行时间最短的候选

        Args:
            group: 候选列表（具有相同的执行结果）

        Returns:
            SQLCandidate: 执行时间最短的候选
        """
        valid = [c for c in group if c.execution_time is not None]
        if not valid:
            return group[0] if group else None
        return min(valid, key=lambda c: c.execution_time)

    def llm_final_decision(self, candidates: List[SQLCandidate],
                           user_query: str) -> Optional[SQLCandidate]:
        """
        使用 LLM 进行最终决策（当没有明显多数时）

        Args:
            candidates: 所有候选 SQL 及其执行结果
            user_query: 用户原始查询

        Returns:
            SQLCandidate: LLM 选择的最佳候选
        """
        if not self.llm_client:
            logger.warning("LLM 未设置，回退选择第一个成功候选")
            successful = [c for c in candidates if c.status == SQLStatus.SUCCESS]
            return successful[0] if successful else None

        # 构建 prompt（统一模板化，调用方零关注 system/user 拆分）
        candidates_text = ""
        for i, cand in enumerate(candidates, 1):
            candidates_text += f"\n候选 {i}:\n"
            candidates_text += f"SQL: {cand.sql}\n"
            if cand.execution_time is not None:
                candidates_text += f"执行时间: {cand.execution_time:.3f}s\n"
            if cand.status == SQLStatus.SUCCESS:
                # 截断结果避免太长
                result_str = str(cand.result)[:500] if cand.result else "空"
                candidates_text += f"结果预览: {result_str}\n"
            else:
                candidates_text += f"状态: 执行失败\n"

        try:
            messages = LLM_FINAL_DECISION_PROMPT.format_messages(
                user_query=user_query,
                candidates_text=candidates_text,
            )
            raw = stream_with_sse(self.llm_client.stream(messages, as_json=True, temperature=0.0))
            result = parse_json(raw)
            selected_idx = result.get("selected", 1)
            if isinstance(selected_idx, str):
                selected_idx = int(''.join(filter(str.isdigit, selected_idx)) or '1')

            # 转为 0-based 索引
            idx = max(0, min(selected_idx - 1, len(candidates) - 1))
            logger.info(f"LLM 选择候选 {selected_idx}: {result.get('reason', '')}")
            return candidates[idx]

        except Exception as e:
            logger.error(f"LLM 决策失败: {e}")
            successful = [c for c in candidates if c.status == SQLStatus.SUCCESS]
            return successful[0] if successful else None

    def decide(self, candidates: List[SQLCandidate],
               user_query: str,
               mschema: List[Any] = None) -> DecisionResult:
        """
        完整的决策流程

        Args:
            candidates: SQLCandidate 列表
            user_query: 用户查询
            mschema: MSchema 表列表（可选，用于结果验证）

        Returns:
            DecisionResult: 决策结果
        """
        if not candidates:
            return DecisionResult(decision_reason="无候选 SQL")

        # 1. 按结果分组
        groups = self.group_by_result(candidates)
        logger.info(f"结果分组: {len(groups)} 组, {dict((k, len(v)) for k, v in groups.items())}")

        # 2. 检查是否全部失败
        successful_groups = {k: v for k, v in groups.items() if k != "__failed__"}
        if not successful_groups:
            return DecisionResult(
                decision_reason="所有候选 SQL 执行失败",
                voting_summary={"groups": len(groups), "all_failed": True},
            )

        # 3. 查找多数组
        has_majority, majority_key, majority_group = self.find_majority_group(groups)

        if has_majority:
            # 多数一致 → 选最快的
            best = self.select_fastest_from_group(majority_group)
            decision = DecisionResult(
                selected_sql=best.sql,
                selected_result=best.result,
                execution_time=best.execution_time,
                decision_reason=f"多数一致（{len(majority_group)}/{sum(len(v) for v in successful_groups.values())}），选择最快",
                voting_summary={
                    "total_groups": len(successful_groups),
                    "majority_size": len(majority_group),
                    "total_successful": sum(len(v) for v in successful_groups.values()),
                },
            )
        else:
            # 全不同 → LLM 决策
            successful_candidates = [c for c in candidates if c.status == SQLStatus.SUCCESS]
            best = self.llm_final_decision(successful_candidates, user_query)

            if best is None:
                return DecisionResult(
                    decision_reason="LLM 决策无法选择",
                    voting_summary={"total_groups": len(successful_groups)},
                )

            decision = DecisionResult(
                selected_sql=best.sql,
                selected_result=best.result,
                execution_time=best.execution_time,
                decision_reason="LLM 最终决策（无多数一致）",
                voting_summary={
                    "total_groups": len(successful_groups),
                    "total_successful": len(successful_candidates),
                    "llm_decided": True,
                },
            )

        # 4. 结果可信度验证（决策 24）
        if self.result_verifier is not None and decision.selected_sql:
            verification = self.result_verifier.verify(
                user_query=user_query,
                selected_sql=decision.selected_sql,
                result_sample=decision.selected_result,
                mschema=mschema,
            )
            if verification.should_reject:
                logger.warning(f"结果验证不可信: {verification.reason}")
                return DecisionResult(
                    decision_reason=f"结果不可信: {verification.reason}",
                    voting_summary={
                        **(decision.voting_summary or {}),
                        "verification": verification.to_dict(),
                        "rejected": True,
                    },
                )

        return decision

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
            raw = stream_with_sse(self.llm_client.stream(prompt_messages, as_json=True, temperature=0.0))
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
            raw = stream_with_sse(self.llm_client.stream(prompt_messages, as_json=True, temperature=0.0))
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

        子图节点：group_by_result → find_majority → (条件) select_fastest | llm_final
        子图输入字段：candidates (List[SQLCandidate]), user_query
        子图输出字段：final_decision (DecisionResult)
        """
        from src.decision.decision_graph import build_decision_graph
        return build_decision_graph(self)

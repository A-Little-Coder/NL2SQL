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
from typing import List, Dict, Any, Optional
from collections import Counter
from loguru import logger

from src.sql_generation.sql_generator import SQLCandidate, SQLStatus


@dataclass
class DecisionResult:
    """决策结果"""
    selected_sql: str = None
    selected_result: Any = None
    execution_time: float = None
    decision_reason: str = None
    voting_summary: Dict[str, Any] = None


class SelfConsistencyDecision:
    """
    Self-Consistency 决策器

    决策逻辑:
    1. 多数结果一致 → 选择执行时间最短的 SQL
    2. 所有结果不同 → 调用 LLM 进行最终决策
    3. 全部失败 → 返回错误

    Attributes:
        llm_client: LLM 客户端
        num_candidates: 候选数量（默认 5）
    """

    def __init__(self, llm_client=None, num_candidates: int = 5):
        self.llm_client = llm_client
        self.num_candidates = num_candidates

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

        # 构建 prompt
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

        prompt = f"""用户查询: "{user_query}"

有以下候选 SQL 及其执行结果：
{candidates_text}

请选择最符合用户查询意图的 SQL，返回 JSON：
{{"selected": 候选编号, "reason": "选择理由"}}"""

        try:
            messages = [
                {"role": "system", "content": "你是 SQL 评审专家，只输出 JSON。"},
                {"role": "user", "content": prompt},
            ]
            result = self.llm_client.chat_json(messages, temperature=0.0)
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
               user_query: str) -> DecisionResult:
        """
        完整的决策流程

        Args:
            candidates: SQLCandidate 列表
            user_query: 用户查询

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
            return DecisionResult(
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

            return DecisionResult(
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

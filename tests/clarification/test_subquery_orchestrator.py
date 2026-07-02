# ============================================================================
# SubqueryOrchestrator + single_query_graph 单元测试
# （决策 14 / refactor-single-query-graph）
# ============================================================================
# 验证：
#   - single_query_graph 正常单意图、cache 命中短路、fail-fast 早退
#   - 多意图串行执行 + 结果收集
#   - 失败隔离：某子查询失败 / 抛异常不中断其他
#   - 单查询流水线胶水只存在于 single_query_graph 一处（orchestrator 仅 invoke）
#
# 运行: pytest tests/clarification/test_subquery_orchestrator.py -v
# ============================================================================

import unittest
from unittest.mock import MagicMock

from src.clarification.subquery_orchestrator import SubqueryOrchestrator, SubqueryResult
from src.graph.single_query_graph import build_single_query_graph


def _make_decision(sql, success=True, path="A"):
    """构造一个 mock DecisionResult。"""
    d = MagicMock()
    d.selected_sql = sql if success else ""
    d.selected_result = [(1,)] if success else None
    d.decision_path = path if success else "FAILED"
    d.fix_failed = False
    d.voting_summary = {}
    d.candidate_scores_r1 = []
    d.candidate_scores_r2 = None
    d.selected_candidate_id = "c1"
    d.fix_rounds_used = 0
    d.last_error = None
    d.decision_reason = ""
    return d


def _make_components(success_map=None):
    """构造 mock 的 retriever/selector/generator/fix_loop/decider/answerability_checker。

    success_map: {subquery: bool} 控制每个子查询是否成功（按 user_query 匹配）。
    """
    success_map = success_map or {}

    def is_success(user_query):
        return success_map.get(user_query, True)

    # IR
    retriever = MagicMock()
    ir_graph = MagicMock()
    ir_graph.invoke = MagicMock(return_value={"retrieved_context": MagicMock()})
    retriever.build_graph = MagicMock(return_value=ir_graph)

    # SS
    selector = MagicMock()
    ss_graph = MagicMock()
    ss_graph.invoke = MagicMock(return_value={"selected_schema": [MagicMock()]})
    selector.build_graph = MagicMock(return_value=ss_graph)

    # CG
    generator = MagicMock()
    cg_graph = MagicMock()
    def _cg_invoke(inp):
        cand = MagicMock()
        cand.sql = "SELECT 1"
        cand.id = "c1"
        cand.status = MagicMock()  # SQLStatus.SUCCESS 由 execution 节点覆盖
        return {"sql_candidates": [cand]}
    cg_graph.invoke = MagicMock(side_effect=_cg_invoke)
    generator.build_graph = MagicMock(return_value=cg_graph)

    # Execution
    fix_loop = MagicMock()
    exec_result = MagicMock()
    exec_result.success = True
    exec_result.result_data = [(1,)]
    exec_result.execution_time = 0.001
    exec_result.error = None
    fix_loop.executor.execute = MagicMock(return_value=exec_result)

    # Decision（按 user_query / candidates 决定成功与否）
    decider = MagicMock()
    dec_graph = MagicMock()
    def _dec_invoke(inp):
        uq = inp.get("user_query", "")
        cands = inp.get("candidates", []) or []
        # 无候选 → 失败决策（模拟 decision 子图全失败分支）
        if not cands:
            return {"final_decision": _make_decision("", success=False, path="FAILED")}
        return {"final_decision": _make_decision("SELECT 1", success=is_success(uq))}
    dec_graph.invoke = MagicMock(side_effect=_dec_invoke)
    decider.build_graph = MagicMock(return_value=dec_graph)

    # Answerability（to_dict 必须返回真 dict，供条件边 answerable 字段判定）
    answerability = MagicMock()
    _ans_ok = MagicMock()
    _ans_ok.should_reject = False
    _ans_ok.reason = ""
    _ans_ok.to_dict = MagicMock(return_value={"answerable": "true", "reason": "", "confidence": None})
    answerability.check = MagicMock(return_value=_ans_ok)

    return retriever, selector, generator, fix_loop, decider, answerability


def _reject_answerability(reason="粒度不匹配"):
    """构造一个拒答的 answerability 结果（to_dict 返回 answerable=false）。"""
    ans = MagicMock()
    ans.should_reject = True
    ans.reason = reason
    ans.to_dict = MagicMock(return_value={"answerable": "false", "reason": reason, "confidence": None})
    return ans


def _build_graph(comps, with_answerability=True):
    """用 mock components 编译 single_query_graph。"""
    retriever, selector, generator, fix_loop, decider, answerability = comps
    return build_single_query_graph(
        retriever=retriever,
        selector=selector,
        generator=generator,
        fix_loop=fix_loop,
        decider=decider,
        answerability_checker=answerability if with_answerability else None,
    )


class TestSingleQueryGraph(unittest.TestCase):
    """单查询流水线图（替代原 run_single_query 函数测试）"""

    def test_success(self):
        comps = _make_components()
        graph = _build_graph(comps)
        out = graph.invoke({"user_query": "查苹果销售额"})
        self.assertEqual(out.get("final_sql"), "SELECT 1")
        self.assertEqual(out.get("decision_path"), "A")

    def test_cache_hit_short_circuit(self):
        """cache 命中 → 跳过 ir/ss/cg 直奔 execution（refactor 单测）"""
        comps = _make_components()
        # cache 命中需要 cached_sql；execution 节点从 cached_sql 构造候选
        graph = _build_graph(comps)
        out = graph.invoke({"user_query": "查苹果", "cache_hit": True, "cached_sql": "SELECT 42"})
        self.assertEqual(out.get("final_sql"), "SELECT 1")  # decision mock 仍返回 SELECT 1

    def test_cache_hit_empty_sql(self):
        """cache 命中但 cached_sql 空 → execution 写 error，无候选 → decision 失败"""
        comps = _make_components()
        graph = _build_graph(comps)
        out = graph.invoke({"user_query": "查苹果", "cache_hit": True, "cached_sql": ""})
        # execution 节点 cache_hit 分支：cached_sql 空 → return error，无候选
        self.assertIn("cache_hit", out.get("error", "") or "")
        self.assertFalse(out.get("final_sql"))

    def test_ir_failure(self):
        """IR 未产出 context → ss 节点返回空 schema → END（fail-fast）"""
        comps = _make_components()
        retriever, *_ = comps
        retriever.build_graph.return_value.invoke.return_value = {"retrieved_context": None}
        graph = _build_graph(comps)
        out = graph.invoke({"user_query": "查苹果"})
        self.assertFalse(out.get("final_sql"))
        self.assertEqual(out.get("selected_schema"), [])

    def test_ss_no_schema(self):
        """SS 未选出 schema → END（fail-fast）"""
        comps = _make_components()
        selector = comps[1]
        selector.build_graph.return_value.invoke.return_value = {"selected_schema": []}
        graph = _build_graph(comps)
        out = graph.invoke({"user_query": "查苹果"})
        self.assertFalse(out.get("final_sql"))
        self.assertEqual(out.get("selected_schema"), [])

    def test_answerability_reject(self):
        """可回答性拒答 → END（fail-fast），rejection_reason 被写入"""
        comps = _make_components()
        answerability = comps[5]
        answerability.check.return_value = _reject_answerability()
        graph = _build_graph(comps)
        out = graph.invoke({"user_query": "查苹果"})
        self.assertFalse(out.get("final_sql"))
        self.assertIn("不可回答", out.get("error", ""))

    def test_cg_no_candidates(self):
        """CG 未产出候选 → END（fail-fast）"""
        comps = _make_components()
        generator = comps[2]
        # 覆盖整个 invoke（原为 side_effect，side_effect 优先于 return_value）
        generator.build_graph.return_value.invoke = MagicMock(return_value={"sql_candidates": []})
        graph = _build_graph(comps)
        out = graph.invoke({"user_query": "查苹果"})
        self.assertFalse(out.get("final_sql"))

    def test_decision_no_sql(self):
        """Decision 未产出 SQL → 失败"""
        comps = _make_components(success_map={"查苹果": False})
        graph = _build_graph(comps)
        out = graph.invoke({"user_query": "查苹果"})
        self.assertFalse(out.get("final_sql"))


class TestSubqueryOrchestrator(unittest.TestCase):
    """多意图编排 + 失败隔离"""

    def test_single_subquery(self):
        """单子查询等价单流程"""
        comps = _make_components()
        orch = SubqueryOrchestrator(_build_graph(comps))
        results = orch.run(["查苹果销售额"], shared_state={})
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].success)

    def test_multi_subqueries_all_success(self):
        """多子查询全部成功"""
        comps = _make_components()
        orch = SubqueryOrchestrator(_build_graph(comps))
        results = orch.run(["查苹果销售额", "查苹果利润"], shared_state={})
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r.success for r in results))

    def test_failure_isolation(self):
        """某子查询失败不中断其他（失败隔离，决策 14）"""
        comps = _make_components(success_map={"失败的查询": False})
        orch = SubqueryOrchestrator(_build_graph(comps))
        results = orch.run(["查苹果销售额", "失败的查询", "查苹果利润"], shared_state={})
        self.assertEqual(len(results), 3)
        self.assertTrue(results[0].success)
        self.assertFalse(results[1].success)
        self.assertTrue(results[2].success)  # 失败后的子查询仍执行

    def test_exception_isolation(self):
        """某子查询抛异常不中断其他"""
        comps = _make_components()
        # 让第二个子查询的 IR 抛异常
        call_count = [0]
        original_invoke = comps[0].build_graph.return_value.invoke

        def _flaky_invoke(inp):
            call_count[0] += 1
            if call_count[0] == 2:
                raise RuntimeError("IR 爆炸")
            return original_invoke(inp)

        comps[0].build_graph.return_value.invoke = MagicMock(side_effect=_flaky_invoke)
        orch = SubqueryOrchestrator(_build_graph(comps))
        results = orch.run(["q1", "q2", "q3"], shared_state={})
        self.assertEqual(len(results), 3)
        self.assertTrue(results[0].success)
        self.assertFalse(results[1].success)
        self.assertIn("IR 爆炸", results[1].error)
        self.assertTrue(results[2].success)

    def test_empty_subqueries(self):
        comps = _make_components()
        orch = SubqueryOrchestrator(_build_graph(comps))
        results = orch.run([], shared_state={})
        self.assertEqual(len(results), 0)

    def test_rejected_maps_to_rejected_path(self):
        """answerability 拒答时 orchestrator 判定 decision_path=REJECTED"""
        comps = _make_components()
        answerability = comps[5]
        answerability.check.return_value = _reject_answerability()
        orch = SubqueryOrchestrator(_build_graph(comps))
        results = orch.run(["查苹果"], shared_state={})
        self.assertFalse(results[0].success)
        self.assertEqual(results[0].decision_path, "REJECTED")


class TestSubqueryResultDataclass(unittest.TestCase):
    """SubqueryResult 数据类"""

    def test_to_dict(self):
        r = SubqueryResult(subquery="q", final_sql="SELECT 1", success=True, decision_path="A")
        d = r.to_dict()
        self.assertEqual(d["subquery"], "q")
        self.assertEqual(d["final_sql"], "SELECT 1")
        self.assertTrue(d["success"])
        self.assertEqual(d["decision_path"], "A")


if __name__ == "__main__":
    unittest.main()

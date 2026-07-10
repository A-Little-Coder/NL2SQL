"""HistoryCache 单元测试

覆盖场景：
- 命中：session_history 匹配
- 命中：metric_definition 匹配
- 未命中：无历史
- 低置信度过滤
- 时间相关 follow-up 不复用
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from src.memory.history_cache import CacheResult, HistoryCache


# ── Mock LLM 客户端 ────────────────────────────────────────────

class MockLLMClient:
    """模拟 LLM 客户端，返回预设 JSON"""

    def __init__(self, preset_result: dict):
        self.preset = preset_result
        self.last_messages = None

    def invoke(self, messages, **kwargs):
        """新接口：as_json=True 时返回 dict"""
        self.last_messages = messages
        return self.preset

    def chat(self, messages, **kwargs):
        """旧接口（向后兼容，本测试已不再用）"""
        self.last_messages = messages
        return self.preset

    def chat_json(self, messages, **kwargs):
        """旧接口（向后兼容）"""
        self.last_messages = messages
        return self.preset


# ── Fixtures ──────────────────────────────────────────────────

@pytest.fixture
def session_history():
    return [
        {"turn_index": 1, "user_query": "查询苹果的销售额", "final_sql": "SELECT SUM(amount) FROM sales WHERE product='Apple'"},
        {"turn_index": 2, "user_query": "查询三星的销售额", "final_sql": "SELECT SUM(amount) FROM sales WHERE product='Samsung'"},
    ]


@pytest.fixture
def metric_definitions():
    return [
        {"name": "销售额", "description": "SUM of amount", "sql_pattern": "SELECT SUM(amount) FROM sales", "confidence": 0.9},
    ]


# ── Test: 命中 session_history ────────────────────────────────

class TestCacheHitSessionHistory:
    """历史命中 — session_history 匹配"""

    def test_hit_exact_match(self):
        """完全相同的查询应命中 session_history"""
        mock = MockLLMClient({
            "can_reuse": True,
            "source": "session_history",
            "cached_sql": "SELECT SUM(amount) FROM sales WHERE product='Apple'",
            "confidence": 0.95,
            "matched_turn_index": 1,
            "reason": "查询与历史轮次 1 完全相同",
        })
        cache = HistoryCache(mock, min_confidence=0.8)
        result = cache.check(
            user_query="查询苹果的销售额",
            session_history=[
                {"turn_index": 1, "user_query": "查询苹果的销售额", "final_sql": "SELECT SUM(amount) FROM sales WHERE product='Apple'"},
            ],
            metric_definitions=[],
        )
        assert result.hit is True
        assert result.source == "session_history"
        assert result.cached_sql is not None
        assert result.confidence >= 0.8
        assert result.historical_query == "查询苹果的销售额"

    def test_hit_with_historical_query_backfill(self):
        """命中时应从 session_history 回填 historical_query"""
        mock = MockLLMClient({
            "can_reuse": True,
            "source": "session_history",
            "cached_sql": "SELECT SUM(amount) FROM sales WHERE product='Apple'",
            "confidence": 0.95,
            "matched_turn_index": 2,
            "reason": "查询与历史轮次 2 等价",
        })
        cache = HistoryCache(mock, min_confidence=0.8)
        result = cache.check(
            user_query="查一下苹果的销售额",
            session_history=[
                {"turn_index": 1, "user_query": "查询三星的销售额", "final_sql": "SELECT SUM(amount) FROM sales WHERE product='Samsung'"},
                {"turn_index": 2, "user_query": "查询苹果的销售额", "final_sql": "SELECT SUM(amount) FROM sales WHERE product='Apple'"},
            ],
            metric_definitions=[],
        )
        assert result.hit is True
        assert result.historical_query == "查询苹果的销售额"

    def test_hit_with_turn_id_instead_of_turn_index(self):
        """兼容 turn_id 字段名"""
        mock = MockLLMClient({
            "can_reuse": True,
            "source": "session_history",
            "cached_sql": "SELECT 1",
            "confidence": 0.9,
            "matched_turn_index": "abc123",
            "reason": "命中",
        })
        cache = HistoryCache(mock, min_confidence=0.8)
        result = cache.check(
            user_query="测试",
            session_history=[
                {"turn_id": "abc123", "user_query": "历史查询", "final_sql": "SELECT 1"},
            ],
            metric_definitions=[],
        )
        assert result.historical_query == "历史查询"

    def test_matched_turn_index_not_found_falls_back_to_sql(self):
        """matched_turn_index 不准时，用 cached_sql 反查兜底回填 historical_query"""
        mock = MockLLMClient({
            "can_reuse": True,
            "source": "session_history",
            "cached_sql": "SELECT 1",
            "confidence": 0.9,
            "matched_turn_index": 999,  # 故意给不存在的索引，触发 cached_sql 兜底
            "reason": "命中",
        })
        cache = HistoryCache(mock, min_confidence=0.8)
        result = cache.check(
            user_query="测试",
            session_history=[
                {"turn_index": 1, "user_query": "历史查询", "final_sql": "SELECT 1"},
            ],
            metric_definitions=[],
        )
        assert result.hit is True
        # matched_turn_index=999 找不到，但 cached_sql 与 turn.final_sql 匹配 -> 兜底回填
        assert result.historical_query == "历史查询"

    def test_matched_turn_index_and_sql_both_not_found(self):
        """matched_turn_index 不准且 cached_sql 也反查不到时，historical_query 为 None"""
        mock = MockLLMClient({
            "can_reuse": True,
            "source": "session_history",
            "cached_sql": "SELECT 999",  # 与任何历史 final_sql 都不匹配
            "confidence": 0.9,
            "matched_turn_index": 999,
            "reason": "命中",
        })
        cache = HistoryCache(mock, min_confidence=0.8)
        result = cache.check(
            user_query="测试",
            session_history=[
                {"turn_index": 1, "user_query": "历史查询", "final_sql": "SELECT 1"},
            ],
            metric_definitions=[],
        )
        assert result.hit is True
        assert result.historical_query is None


# ── Test: 命中 metric_definition ──────────────────────────────

class TestCacheHitMetric:
    """历史命中 — metric_definition 匹配"""

    def test_hit_metric_definition(self, metric_definitions):
        """当前查询可用已知指标回答"""
        mock = MockLLMClient({
            "can_reuse": True,
            "source": "metric_definition",
            "cached_sql": "SELECT SUM(amount) FROM sales",
            "confidence": 0.9,
            "matched_turn_index": None,
            "reason": "当前查询匹配已知指标定义「销售额」",
        })
        cache = HistoryCache(mock, min_confidence=0.8)
        result = cache.check(
            user_query="查一下总销售额",
            session_history=[],
            metric_definitions=metric_definitions,
        )
        assert result.hit is True
        assert result.source == "metric_definition"
        assert result.confidence >= 0.8

    def test_metric_definition_hit_has_no_historical_query(self, metric_definitions):
        """metric_definition 命中时 historical_query 应为 None"""
        mock = MockLLMClient({
            "can_reuse": True,
            "source": "metric_definition",
            "cached_sql": "SELECT SUM(amount) FROM sales",
            "confidence": 0.9,
            "matched_turn_index": None,
            "reason": "匹配指标",
        })
        cache = HistoryCache(mock, min_confidence=0.8)
        result = cache.check(
            user_query="查一下总销售额",
            session_history=[
                {"turn_index": 1, "user_query": "历史查询", "final_sql": "SELECT 1"},
            ],
            metric_definitions=metric_definitions,
        )
        assert result.hit is True
        assert result.historical_query is None


# ── Test: 未命中 ──────────────────────────────────────────────

class TestCacheMiss:
    """未命中"""

    def test_no_history_no_metrics(self):
        """无历史也无指标定义时应返回未命中"""
        mock = MockLLMClient({})
        cache = HistoryCache(mock, min_confidence=0.8)
        result = cache.check(
            user_query="查询苹果的销售额",
            session_history=[],
            metric_definitions=[],
        )
        assert result.hit is False

    def test_llm_says_no(self):
        """LLM 判断不能复用"""
        mock = MockLLMClient({
            "can_reuse": False,
            "source": None,
            "cached_sql": None,
            "confidence": 0.0,
            "matched_turn_index": None,
            "reason": "当前查询与历史不匹配",
        })
        cache = HistoryCache(mock, min_confidence=0.8)
        result = cache.check(
            user_query="查询苹果的销售额",
            session_history=[],
            metric_definitions=[],
        )
        assert result.hit is False
        assert result.historical_query is None


# ── Test: 低置信度过滤 ────────────────────────────────────────

class TestLowConfidence:
    """低置信度过滤"""

    def test_below_min_confidence(self):
        """置信度低于阈值应返回未命中"""
        mock = MockLLMClient({
            "can_reuse": True,
            "source": "session_history",
            "cached_sql": "SELECT ...",
            "confidence": 0.7,
            "matched_turn_index": 1,
            "reason": "有点相似但不确定",
        })
        cache = HistoryCache(mock, min_confidence=0.8)
        result = cache.check(
            user_query="查询苹果的销售额",
            session_history=[{"turn_index": 1, "user_query": "查询苹果的销售额", "final_sql": "SELECT ..."}],
            metric_definitions=[],
        )
        assert result.hit is False

    def test_empty_cached_sql(self):
        """can_reuse=True 但 cached_sql 为空时应返回未命中"""
        mock = MockLLMClient({
            "can_reuse": True,
            "source": "session_history",
            "cached_sql": None,
            "confidence": 0.9,
            "matched_turn_index": 1,
            "reason": "有命中但无 SQL",
        })
        cache = HistoryCache(mock, min_confidence=0.8)
        result = cache.check(
            user_query="测试查询",
            session_history=[{"turn_index": 1, "user_query": "测试查询", "final_sql": ""}],
            metric_definitions=[],
        )
        assert result.hit is False

    def test_empty_cached_sql(self):
        """can_reuse=True 但 cached_sql 为空时应返回未命中"""
        mock = MockLLMClient({
            "can_reuse": True,
            "source": "session_history",
            "cached_sql": None,
            "confidence": 0.9,
            "reason": "有命中但无 SQL",
        })
        cache = HistoryCache(mock, min_confidence=0.8)
        result = cache.check(
            user_query="测试查询",
            session_history=[{"turn_index": 1, "user_query": "测试查询", "final_sql": ""}],
            metric_definitions=[],
        )
        assert result.hit is False


# ── Test: 值参数变化 follow-up ─────────────────────────────────

class TestValueParameterRelatedFollowUp:
    """值参数变化的 follow-up 仍可复用（交由 value_rewrite 改写）"""

    def test_llm_allows_time_change_reuse(self):
        """时间变化的 follow-up 应返回命中，historical_query 回填"""
        mock = MockLLMClient({
            "can_reuse": True,
            "source": "session_history",
            "cached_sql": "SELECT SUM(amount) FROM sales WHERE year=2025",
            "confidence": 0.9,
            "matched_turn_index": 1,
            "reason": "意图等价，值参数差异交由值改写阶段处理",
        })
        cache = HistoryCache(mock, min_confidence=0.8)
        result = cache.check(
            user_query="那去年的呢",
            session_history=[{"turn_index": 1, "user_query": "查询今年的销售额", "final_sql": "SELECT SUM(amount) FROM sales WHERE year=2025"}],
            metric_definitions=[],
        )
        assert result.hit is True
        assert result.historical_query == "查询今年的销售额"
        assert result.cached_sql is not None

    def test_llm_allows_region_change_reuse(self):
        """地区变化的 follow-up 应返回命中，historical_query 回填"""
        mock = MockLLMClient({
            "can_reuse": True,
            "source": "session_history",
            "cached_sql": "SELECT SUM(amount) FROM sales WHERE region='华北'",
            "confidence": 0.9,
            "matched_turn_index": 1,
            "reason": "意图等价，值参数差异交由值改写阶段处理",
        })
        cache = HistoryCache(mock, min_confidence=0.8)
        result = cache.check(
            user_query="那华北区的呢",
            session_history=[{"turn_index": 1, "user_query": "查询华东区的销售额", "final_sql": "SELECT SUM(amount) FROM sales WHERE region='华东'"}],
            metric_definitions=[],
        )
        assert result.hit is True
        assert result.historical_query == "查询华东区的销售额"
        assert result.cached_sql is not None

    def test_llm_allows_limit_change_reuse(self):
        """LIMIT 值变化的 follow-up 应返回命中，historical_query 回填"""
        mock = MockLLMClient({
            "can_reuse": True,
            "source": "session_history",
            "cached_sql": "SELECT product FROM sales ORDER BY amount DESC LIMIT 20",
            "confidence": 0.9,
            "matched_turn_index": 1,
            "reason": "意图等价，值参数差异交由值改写阶段处理",
        })
        cache = HistoryCache(mock, min_confidence=0.8)
        result = cache.check(
            user_query="那前20名呢",
            session_history=[{"turn_index": 1, "user_query": "查询销售额前10名的产品", "final_sql": "SELECT product FROM sales ORDER BY amount DESC LIMIT 10"}],
            metric_definitions=[],
        )
        assert result.hit is True
        assert result.historical_query == "查询销售额前10名的产品"
        assert result.cached_sql is not None

    def test_llm_disallows_structure_change(self):
        """结构变化（增删 WHERE 谓词）不应命中"""
        mock = MockLLMClient({
            "can_reuse": False,
            "source": None,
            "cached_sql": None,
            "confidence": 0.5,
            "matched_turn_index": None,
            "reason": "结构变化，增删了 WHERE 谓词，不应复用",
        })
        cache = HistoryCache(mock, min_confidence=0.8)
        result = cache.check(
            user_query="查询华东区今年的销售额",
            session_history=[{"turn_index": 1, "user_query": "查询今年的销售额", "final_sql": "SELECT SUM(amount) FROM sales WHERE year=2025"}],
            metric_definitions=[],
        )
        assert result.hit is False

    def test_llm_disallows_intent_change(self):
        """意图变化（销售额→利润）不应命中"""
        mock = MockLLMClient({
            "can_reuse": False,
            "source": None,
            "cached_sql": None,
            "confidence": 0.3,
            "matched_turn_index": None,
            "reason": "意图变化，指标不同，不应复用",
        })
        cache = HistoryCache(mock, min_confidence=0.8)
        result = cache.check(
            user_query="查询利润",
            session_history=[{"turn_index": 1, "user_query": "查询销售额", "final_sql": "SELECT SUM(amount) FROM sales"}],
            metric_definitions=[],
        )
        assert result.hit is False


# ── Test: LLM 调用失败降级 ──────────────────────────────────

class TestLLMFailure:
    """LLM 调用失败时安全降级"""

    def test_llm_raises_exception(self):
        """LLM 异常应返回未命中"""
        failing_client = MagicMock()
        failing_client.invoke.side_effect = RuntimeError("API 调用失败")

        cache = HistoryCache(failing_client, min_confidence=0.8)
        result = cache.check(
            user_query="测试查询",
            session_history=[{"turn_index": 1, "user_query": "测试查询", "final_sql": "SELECT 1"}],
            metric_definitions=[],
        )
        assert result.hit is False

    def test_llm_returns_invalid_json(self):
        """LLM 返回无效 JSON 时应返回未命中"""

        class InvalidClient:
            def invoke(self, messages, **kwargs):
                # invoke(as_json=True) 失败时会经过 parse_json 兜底，
                # 返回 {"raw_response": ...} dict
                return {"raw_response": "not valid json{{{"}

        cache = HistoryCache(InvalidClient(), min_confidence=0.8)
        result = cache.check(
            user_query="测试查询",
            session_history=[{"turn_index": 1, "user_query": "测试查询", "final_sql": "SELECT 1"}],
            metric_definitions=[],
        )
        assert result.hit is False


# ── Test: CacheResult 数据结构 ────────────────────────────────

class TestCacheResultData:
    """CacheResult 数据类"""

    def test_default_values(self):
        """默认值应为 False/None/0.0"""
        r = CacheResult()
        assert r.hit is False
        assert r.cached_sql is None
        assert r.source is None
        assert r.confidence == 0.0
        assert r.historical_query is None

    def test_custom_values(self):
        """自定义值应正确设置"""
        r = CacheResult(
            hit=True,
            cached_sql="SELECT 1",
            source="session_history",
            confidence=0.95,
            historical_query="历史查询",
        )
        assert r.hit is True
        assert r.cached_sql == "SELECT 1"
        assert r.source == "session_history"
        assert r.confidence == 0.95
        assert r.historical_query == "历史查询"

    def test_miss_has_no_historical_query(self):
        """未命中时 historical_query 保持 None"""
        r = CacheResult(
            hit=False,
            cached_sql=None,
            source=None,
            confidence=0.0,
            historical_query=None,
        )
        assert r.hit is False
        assert r.historical_query is None


# ── Test: Prompt 构建 ─────────────────────────────────────────

class TestPromptBuilding:
    """Prompt 构建验证"""

    def test_format_history(self):
        """_format_history 应生成正确格式"""
        mock = MockLLMClient({"can_reuse": False})
        cache = HistoryCache(mock)

        formatted = cache._format_history([
            {"turn_index": 1, "user_query": "查询A", "final_sql": "SELECT * FROM A"},
        ])
        assert "轮次 1" in formatted
        assert "查询A" in formatted
        assert "SELECT * FROM A" in formatted

    def test_format_metrics(self):
        """_format_metrics 应生成正确格式"""
        mock = MockLLMClient({"can_reuse": False})
        cache = HistoryCache(mock)

        formatted = cache._format_metrics([
            {"name": "销售额", "description": "总销售", "sql_pattern": "SELECT SUM(amount) FROM sales"},
        ])
        assert "销售额" in formatted
        assert "总销售" in formatted
        assert "SELECT SUM(amount)" in formatted

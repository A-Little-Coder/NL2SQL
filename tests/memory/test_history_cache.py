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
            "reason": "当前查询与历史不匹配",
        })
        cache = HistoryCache(mock, min_confidence=0.8)
        result = cache.check(
            user_query="查询苹果的销售额",
            session_history=[],
            metric_definitions=[],
        )
        assert result.hit is False


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
            "reason": "有命中但无 SQL",
        })
        cache = HistoryCache(mock, min_confidence=0.8)
        result = cache.check(
            user_query="测试查询",
            session_history=[{"turn_index": 1, "user_query": "测试查询", "final_sql": ""}],
            metric_definitions=[],
        )
        assert result.hit is False


# ── Test: 时间相关 follow-up ─────────────────────────────────

class TestTimeRelatedFollowUp:
    """时间相关的 follow-up 不复用"""

    def test_llm_rejects_time_change(self):
        """时间变化的 follow-up 应返回未命中"""
        mock = MockLLMClient({
            "can_reuse": False,
            "source": None,
            "cached_sql": None,
            "confidence": 0.0,
            "reason": "涉及时间范围变化",
        })
        cache = HistoryCache(mock, min_confidence=0.8)
        result = cache.check(
            user_query="那去年的呢",
            session_history=[{"turn_index": 1, "user_query": "查询今年的销售额", "final_sql": "SELECT ..."}],
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

    def test_custom_values(self):
        """自定义值应正确设置"""
        r = CacheResult(
            hit=True,
            cached_sql="SELECT 1",
            source="session_history",
            confidence=0.95,
        )
        assert r.hit is True
        assert r.cached_sql == "SELECT 1"
        assert r.source == "session_history"
        assert r.confidence == 0.95


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

"""UserMemory 单元测试"""

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from src.memory.user_memory import UserMemory, ALLOWED_TOPICS


@pytest.fixture
def temp_dir():
    path = tempfile.mkdtemp()
    yield path
    shutil.rmtree(path)


@pytest.fixture
def memory(temp_dir):
    mem = UserMemory(user_id="test_user", base_dir=temp_dir)
    return mem


class TestUserMemoryBasic:
    """基础操作测试"""

    def test_create_and_load(self, temp_dir):
        """新用户首次访问应创建初始记忆文件"""
        mem = UserMemory(user_id="alice_001", base_dir=temp_dir)
        data = mem.load()

        assert data["user_id"] == "alice_001"
        assert "created_at" in data
        assert "updated_at" in data
        assert data["term_preferences"] == {}
        assert data["clarification_history"] == []
        assert data["frequently_used_tables"] == {}
        assert data["metric_definitions"] == {}
        assert data["query_preferences"] == {}
        assert data["domain_context"] == {}

        # 验证文件已创建
        file_path = Path(temp_dir) / "alice_001.json"
        assert file_path.exists()

    def test_load_twice_no_overwrite(self, memory):
        """多次 load 不应覆盖已有数据"""
        memory.load()
        memory.record_term_preference("销售额", "gmv", 0.9)

        mem2 = UserMemory(user_id="test_user", base_dir=memory._storage.base_dir)
        data2 = mem2.load()
        assert data2["term_preferences"]["销售额"]["resolved_to"] == "gmv"

    def test_save_persistence(self, memory):
        """save 后重新加载应能读取到最新数据"""
        memory.load()
        memory.record_term_preference("苹果", "fruit_apple", 0.8)
        memory.save()

        # 重新加载
        mem2 = UserMemory(user_id="test_user", base_dir=memory._storage.base_dir)
        data2 = mem2.load()
        assert data2["term_preferences"]["苹果"]["resolved_to"] == "fruit_apple"

    def test_missing_dir_auto_create(self, temp_dir):
        """目录不存在时应自动创建"""
        deep_dir = os.path.join(temp_dir, "a", "b", "c")
        mem = UserMemory(user_id="test", base_dir=deep_dir)
        data = mem.load()
        assert data["user_id"] == "test"
        assert os.path.isdir(deep_dir)


class TestTermPreferences:
    """术语偏好测试"""

    def test_record_and_get(self, memory):
        memory.load()
        memory.record_term_preference("销售额", "gmv", 0.9)

        pref = memory.get_term_preference("销售额")
        assert pref is not None
        assert pref["resolved_to"] == "gmv"
        assert pref["confidence"] == 0.9
        assert pref["source"] == "user_taught"
        assert "last_used" in pref

    def test_get_nonexistent(self, memory):
        memory.load()
        pref = memory.get_term_preference("不存在的词")
        assert pref is None

    def test_updated_at_refreshed(self, memory):
        memory.load()
        old_updated = memory._data["updated_at"]
        import time
        time.sleep(1.1)  # 确保下一秒
        memory.record_term_preference("测试", "test", 0.5)
        assert memory._data["updated_at"] != old_updated


class TestClarificationHistory:
    """澄清历史测试"""

    def test_append_clarification(self, memory):
        memory.load()
        entry = {
            "original_query": "查一下苹果的销售额",
            "trigger_type": "B_semantic_mismatch",
            "question_asked": "您指的'苹果'是水果还是品牌？",
            "user_answer": "水果",
            "resolved_mapping": {"苹果": "fruit_apple"},
        }
        memory.append_clarification(entry)
        assert len(memory._data["clarification_history"]) == 1
        assert "timestamp" in memory._data["clarification_history"][0]

    def test_append_multiple(self, memory):
        memory.load()
        for i in range(3):
            memory.append_clarification({"query": f"query_{i}"})
        assert len(memory._data["clarification_history"]) == 3


class TestFrequentlyUsedTables:
    """常用表测试"""

    def test_record_table_usage(self, memory):
        memory.load()
        memory.record_table_usage("sales")
        tables = memory._data["frequently_used_tables"]
        assert tables["sales"]["query_count"] == 1

    def test_record_table_usage_increment(self, memory):
        memory.load()
        for _ in range(3):
            memory.record_table_usage("sales")
        tables = memory._data["frequently_used_tables"]
        assert tables["sales"]["query_count"] == 3

    def test_get_frequently_used_tables(self, memory):
        memory.load()
        memory.record_table_usage("orders")
        memory.record_table_usage("sales")
        memory.record_table_usage("sales")
        memory.record_table_usage("products")
        memory.record_table_usage("sales")

        top = memory.get_frequently_used_tables(top_k=2)
        assert top == ["sales", "orders"]  # sales=3, orders=1


class TestMetricDefinitions:
    """指标定义测试"""

    def test_record_auto_learned(self, memory):
        memory.load()
        memory.record_metric_definition(
            "GMV", "完成订单金额总和",
            "SUM(order_amount) WHERE status='completed'",
            source="auto_learned", confidence=0.5,
        )
        metrics = memory._data["metric_definitions"]
        assert metrics["GMV"]["source"] == "auto_learned"
        assert metrics["GMV"]["confidence"] == 0.5

    def test_auto_learned_confidence_increment(self, memory):
        memory.load()
        for _ in range(3):
            memory.record_metric_definition(
                "GMV", "完成订单金额总和",
                "SUM(order_amount) WHERE status='completed'",
                source="auto_learned", confidence=0.5,
            )
        # 每次 +0.1，初始 0.5，第 3 次 = 0.5 + 0.1*2 = 0.7
        assert memory._data["metric_definitions"]["GMV"]["confidence"] == 0.7

    def test_user_taught_overrides_auto_learned(self, memory):
        memory.load()
        # 先自动学习
        memory.record_metric_definition("GMV", "auto desc", "pattern1",
                                        source="auto_learned", confidence=0.5)
        # 用户教
        memory.record_metric_definition("GMV", "用户教的描述", "pattern2",
                                        source="user_taught", confidence=0.95)
        metric = memory._data["metric_definitions"]["GMV"]
        assert metric["source"] == "user_taught"
        assert metric["confidence"] == 0.95
        assert metric["description"] == "用户教的描述"

    def test_user_taught_not_overwritten_by_auto(self, memory):
        memory.load()
        memory.record_metric_definition("GMV", "用户教的", "pattern",
                                        source="user_taught", confidence=0.95)
        # auto_learned 尝试覆盖
        memory.record_metric_definition("GMV", "自动学的", "pattern2",
                                        source="auto_learned", confidence=0.5)
        assert memory._data["metric_definitions"]["GMV"]["source"] == "user_taught"

    def test_get_metric_definitions_with_confidence_filter(self, memory):
        memory.load()
        memory.record_metric_definition("M1", "d1", "p1",
                                        source="auto_learned", confidence=0.5)
        memory.record_metric_definition("M2", "d2", "p2",
                                        source="auto_learned", confidence=0.8)
        memory.record_metric_definition("M3", "d3", "p3",
                                        source="user_taught", confidence=0.95)

        result = memory.get_metric_definitions(min_confidence=0.7)
        names = [r["name"] for r in result]
        assert "M1" not in names  # 0.5 < 0.7
        assert "M2" in names
        assert "M3" in names


class TestQueryPreferences:
    """查询偏好测试"""

    def test_update_and_get(self, memory):
        memory.load()
        memory.update_query_preference("default_time_range", "last_30_days")
        prefs = memory.get_query_preferences()
        assert prefs["default_time_range"] == "last_30_days"


class TestDomainContext:
    """领域上下文测试"""

    def test_update_and_get(self, memory):
        memory.load()
        memory.update_domain_context(
            industry="生鲜电商",
            department="运营部",
            focus_areas=["销售分析", "用户增长"],
        )
        ctx = memory.get_domain_context()
        assert ctx["industry"] == "生鲜电商"
        assert ctx["department"] == "运营部"


class TestUserMemorySchemaGovernance:
    """UserMemory 固定 schema 与污染字段过滤"""

    def test_load_normalizes_missing_and_unknown_topics(self, temp_dir):
        file_path = Path(temp_dir) / "legacy.json"
        file_path.write_text(json.dumps({
            "user_id": "legacy",
            "term_preferences": {"GMV": {"resolved_to": "gmv"}},
            "unknown_topic": {"x": 1},
        }, ensure_ascii=False), encoding="utf-8")

        mem = UserMemory(user_id="legacy", base_dir=temp_dir)
        data = mem.load()
        mem.save()
        saved = json.loads(file_path.read_text(encoding="utf-8"))

        for topic in ALLOWED_TOPICS:
            assert topic in data
            assert topic in saved
        assert "unknown_topic" not in saved

    def test_block_few_shot_and_result_data(self, memory):
        memory.load()
        memory.update_domain_context(industry="零售", few_shot_examples=[{"sql": "SELECT 1"}])
        memory.append_clarification({"question": "q", "final_result": [{"x": 1}], "answer": "a"})
        memory.record_metric_definition(
            "M", "desc", "SELECT SUM(x) FROM t", confidence=0.9
        )
        memory._data["metric_definitions"]["M"]["examples"] = [{"sql": "SELECT 1"}]
        memory.save()

        saved = memory.load()
        assert "few_shot_examples" not in saved["domain_context"]
        assert "final_result" not in saved["clarification_history"][0]
        assert "examples" not in memory.get_metric_definitions(min_confidence=0.0)[0]

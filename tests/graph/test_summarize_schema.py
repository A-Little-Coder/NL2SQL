"""D3（change: enhance-ir-display-and-layout）: _summarize_schema 重构测试

验证 schema_recall 事件 payload 按关键词组聚合：
- 每组含 phrase/terms/columns(含 score)/values(含 score，按 source_phrase 归属)
- 无召回数据的关键词组仍保留（columns/values 为空数组）
- ctx=None 返回空 keyword_groups
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.retrieval.information_retrieval import (
    RetrievedContext,
    RetrievedItem,
    KeywordGroup,
)
from src.graph.main_graph import _summarize_schema


def test_summarize_schema_keyword_groups_aggregation():
    """_summarize_schema 按关键词组聚合 columns 与 values"""
    kw_groups = [
        KeywordGroup(phrase="各科score", terms=["各科score", "subject score"]),
        KeywordGroup(phrase="学校总数", terms=["学校总数", "school count"]),
    ]
    columns = [
        RetrievedItem(item_type="column", name="AvgScrRead", table_name="satscores", score=0.92),
        RetrievedItem(item_type="column", name="AvgScrMath", table_name="satscores", score=0.88),
        RetrievedItem(item_type="column", name="school_id", table_name="schools", score=0.85),
    ]
    kw_map = {
        "各科score": ["satscores.AvgScrRead", "satscores.AvgScrMath"],
        "学校总数": ["schools.school_id"],
    }
    values = [
        RetrievedItem(item_type="value", name="Lincoln High", table_name="schools",
                      score=0.78,
                      metadata={"column_name": "school_name", "source_phrase": "学校总数"}),
        RetrievedItem(item_type="value", name="Roosevelt", table_name="schools",
                      score=0.71,
                      metadata={"column_name": "school_name", "source_phrase": "学校总数"}),
    ]
    ctx = RetrievedContext(
        tables=[], columns=columns, values=values,
        keywords=[], keyword_groups=kw_groups,
        keyword_columns_map=kw_map,
        lsh_hit_count=2, vector_top_scores=[],
    )

    result = _summarize_schema(ctx)

    assert "keyword_groups" in result
    assert len(result["keyword_groups"]) == 2

    g1 = result["keyword_groups"][0]
    assert g1["phrase"] == "各科score"
    assert g1["terms"] == ["各科score", "subject score"]
    assert len(g1["columns"]) == 2
    assert g1["columns"][0] == {"table": "satscores", "column": "AvgScrRead", "score": 0.92}
    # 各科score 组无 value（values 都归属 学校总数）
    assert g1["values"] == []

    g2 = result["keyword_groups"][1]
    assert g2["phrase"] == "学校总数"
    assert len(g2["columns"]) == 1
    assert g2["columns"][0]["column"] == "school_id"
    # values 按 source_phrase 归属到 学校总数
    assert len(g2["values"]) == 2
    assert g2["values"][0]["value"] == "Lincoln High"
    assert g2["values"][0]["column"] == "school_name"
    assert g2["values"][0]["score"] == 0.78


def test_summarize_schema_empty_group_preserved():
    """无召回数据的关键词组仍保留，columns/values 为空数组"""
    kw_groups = [KeywordGroup(phrase="空组", terms=["空组"])]
    ctx = RetrievedContext(
        tables=[], columns=[], values=[],
        keywords=[], keyword_groups=kw_groups,
        keyword_columns_map={},
        lsh_hit_count=0, vector_top_scores=[],
    )
    result = _summarize_schema(ctx)
    assert len(result["keyword_groups"]) == 1
    g = result["keyword_groups"][0]
    assert g["phrase"] == "空组"
    assert g["columns"] == []
    assert g["values"] == []


def test_summarize_schema_none_ctx():
    """ctx=None 返回空 keyword_groups"""
    result = _summarize_schema(None)
    assert result == {"keyword_groups": []}

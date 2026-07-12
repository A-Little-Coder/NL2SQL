"""D2（change: enhance-ir-display-and-layout）: retrieve_values 值召回组归属测试

验证：
- metadata 标注 source_phrase/source_term
- 多 term 命中同一 value 按 LSH jaccard_score 最高归属
- 不传 term_phrase_map 时向后兼容（source_phrase 为空）
- term 不在 map 中时 source_phrase 为空（边界）
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.retrieval.information_retrieval import InformationRetrieval


def _make_ir_with_lsh_mock(lsh_query_return, lsh_threshold=0.3):
    """构造带 mock LSH 索引器的 IR（无向量器，走降级路径便于控制 lsh_score）"""
    ir = InformationRetrieval(lsh_threshold=lsh_threshold)
    mock_lsh = MagicMock()
    mock_lsh._loaded_lsh = MagicMock()
    mock_lsh._loaded_minhashes = MagicMock()
    mock_lsh.query.return_value = lsh_query_return
    ir.lsh_indexer = mock_lsh
    return ir


def test_source_phrase_annotation_single_group():
    """单组检索：value 的 metadata.source_phrase/source_term 正确标注"""
    ir = _make_ir_with_lsh_mock({"products": {"name": ["苹果"]}}, lsh_threshold=0.3)
    with patch('src.retrieval.information_retrieval.LSHIndexer') as MockLSH:
        MockLSH.create_minhash.return_value = MagicMock()
        MockLSH.jaccard_similarity.return_value = 0.7
        result = ir.retrieve_values(
            ["苹果"],
            term_phrase_map={"苹果": "水果"},
        )
    assert len(result) == 1
    item = result[0]
    assert item.metadata["source_phrase"] == "水果"
    assert item.metadata["source_term"] == "苹果"
    assert item.metadata["lsh_jaccard_score"] == 0.7


def test_multi_term_highest_score_wins():
    """多 term 命中同一 value：归属到 LSH 分数最高的 term 所属 phrase"""
    ir = _make_ir_with_lsh_mock({"products": {"name": ["苹果"]}}, lsh_threshold=0.3)
    # 两个 term 都命中同一 value "苹果"："苹果"->0.5(组水果), "apple"->0.8(组fruit)
    with patch('src.retrieval.information_retrieval.LSHIndexer') as MockLSH:
        MockLSH.create_minhash.return_value = MagicMock()
        MockLSH.jaccard_similarity.side_effect = [0.5, 0.8]
        result = ir.retrieve_values(
            ["苹果", "apple"],
            term_phrase_map={"苹果": "水果", "apple": "fruit"},
        )
    assert len(result) == 1  # 同一 value 去重
    item = result[0]
    # 0.8 > 0.5，归属到 "apple" -> "fruit"
    assert item.metadata["source_term"] == "apple"
    assert item.metadata["source_phrase"] == "fruit"
    assert item.metadata["lsh_jaccard_score"] == 0.8


def test_no_term_phrase_map_backward_compat():
    """不传 term_phrase_map：source_phrase 为空，向后兼容"""
    ir = _make_ir_with_lsh_mock({"products": {"name": ["苹果"]}}, lsh_threshold=0.3)
    with patch('src.retrieval.information_retrieval.LSHIndexer') as MockLSH:
        MockLSH.create_minhash.return_value = MagicMock()
        MockLSH.jaccard_similarity.return_value = 0.7
        result = ir.retrieve_values(["苹果"])  # 不传 map
    assert len(result) == 1
    assert result[0].metadata["source_phrase"] == ""
    assert result[0].metadata["source_term"] == "苹果"


def test_value_unassigned_when_term_not_in_map():
    """term 不在 term_phrase_map 中：source_phrase 为空（边界）"""
    ir = _make_ir_with_lsh_mock({"products": {"name": ["苹果"]}}, lsh_threshold=0.3)
    with patch('src.retrieval.information_retrieval.LSHIndexer') as MockLSH:
        MockLSH.create_minhash.return_value = MagicMock()
        MockLSH.jaccard_similarity.return_value = 0.7
        result = ir.retrieve_values(
            ["苹果"],
            term_phrase_map={"其他": "其他组"},  # 不含 "苹果"
        )
    assert len(result) == 1
    assert result[0].metadata["source_phrase"] == ""  # map.get("苹果", "")

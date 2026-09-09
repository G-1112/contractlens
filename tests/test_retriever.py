"""法规 RAG 检索器测试（FR-04）：bigram TF-IDF + 余弦相似度 + 合同类型推断。"""

from __future__ import annotations

from collections import Counter

from api.engine.retriever import (
    RegulationRetriever,
    _bigrams,
    _cosine,
    _infer_contract_type,
)
from api.schemas import Clause


def _clause(text: str) -> Clause:
    return Clause(id="c1", text=text, page=1, level=2)


def test_bigrams():
    bg = _bigrams("押金不退")
    assert isinstance(bg, Counter)
    assert bg["押金"] == 1
    assert bg["金不"] == 1
    assert bg["不退"] == 1


def test_cosine_identical():
    assert _cosine(_bigrams("押金"), _bigrams("押金")) > 0.99


def test_cosine_empty():
    assert _cosine(Counter(), Counter()) == 0.0
    assert _cosine(_bigrams("押金"), Counter()) == 0.0


def test_infer_contract_type():
    assert _infer_contract_type(_clause("甲方应按时为乙方缴纳社会保险与工资。")) == "labor"
    assert _infer_contract_type(_clause("实习生应服从培养安排。")) == "intern"
    assert _infer_contract_type(_clause("押金为三个月租金。")) == "rent"


def test_load_missing_regulation_file(tmp_path):
    r = RegulationRetriever(regulations_dir=tmp_path)
    r.load("rent")  # 文件不存在 → 空列表，不抛异常
    assert r._snippets["rent"] == []


def test_retrieve_for_returns_topk():
    r = RegulationRetriever()
    refs = r.retrieve_for(_clause("押金为三个月租金，押金概不退还。"), "rent", top_k=3)
    assert isinstance(refs, list)
    assert len(refs) <= 3
    # 返回的片段带 [标题 · 章节] 前缀
    if refs:
        assert all("[" in s for s in refs)


def test_retrieve_for_empty_clause():
    r = RegulationRetriever()
    refs = r.retrieve_for(_clause(""), "rent", top_k=3)
    assert refs == []


def test_retrieve_uses_infer():
    r = RegulationRetriever()
    # 无显式类型时按条款内容推断（"工资" → labor）
    refs = r.retrieve(_clause("工资应按时足额发放。"), top_k=3)
    assert isinstance(refs, list)

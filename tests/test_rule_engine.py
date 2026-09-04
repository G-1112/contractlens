"""规则引擎测试（FR-05）：三类检测 —— 关键词 / 数值阈值 / 缺失。"""

from api.engine.rule_engine import RuleEngine
from api.schemas import Clause


def _clause(text: str, cid: str = "c-001-01") -> Clause:
    return Clause(id=cid, text=text, page=1, level=2)


def _ids(hits) -> set[str]:
    return {h.rule_id for h in hits}


def test_keyword_match():
    eng = RuleEngine().load("rent")
    hits = eng.match_clauses([_clause("若乙方提前退租，押金概不退还。")])
    assert "R02" in _ids(hits)


def test_keyword_negation_suppresses():
    """含排除词时不应触发（R03 房东单方涨租，但经双方协商则不触发）。"""
    eng = RuleEngine().load("rent")
    hits = eng.match_clauses([_clause("甲方有权调整租金，经双方协商一致后执行。")])
    assert "R03" not in _ids(hits)


def test_threshold_numeric():
    """押金三个月 > 两个月 → R01 触发。"""
    eng = RuleEngine().load("rent")
    hits = eng.match_clauses([_clause("押金为三个月租金，共计 9000 元。")])
    assert "R01" in _ids(hits)


def test_threshold_below_limit_not_fired():
    """押金一个月，不超过两个月 → R01 不触发。"""
    eng = RuleEngine().load("rent")
    hits = eng.match_clauses([_clause("押金为一个月租金。")])
    assert "R01" not in _ids(hits)


def test_missing_detection():
    """文档未出现"维修"→ R05 缺失检测触发，clause_id 以 missing- 开头。"""
    eng = RuleEngine().load("rent")
    hits = eng.match_document([_clause("乙方应按时支付租金。")])
    missing = [h for h in hits if h.clause_id.startswith("missing-")]
    assert "R05" in _ids(missing)


def test_missing_detection_not_fired_when_present():
    eng = RuleEngine().load("rent")
    hits = eng.match_document([_clause("房屋维修由甲方负责。")])
    missing = [h for h in hits if h.rule_id == "R05"]
    assert missing == []


def test_rule_hit_carries_risk_type():
    """RuleHit 携带 risk_type，供融合层在 LLM 缺失类型时兜底。"""
    eng = RuleEngine().load("rent")
    hits = eng.match_clauses([_clause("押金概不退还。")])
    assert any(h.risk_type == "押金" for h in hits)

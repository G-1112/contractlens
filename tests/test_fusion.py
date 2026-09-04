"""双引擎融合策略测试（FR-07）。

验收：20 条规则与 LLM 冲突样本，融合结果 100% 符合上表策略。
下表覆盖全部 4 种来源组合。
"""

from api.engine.fusion import fuse
from api.schemas import Clause, LlmJudgement, RuleHit


def _clause(cid: str, text: str = "条款文本") -> Clause:
    return Clause(id=cid, text=text, page=1, level=2)


def test_both_when_llm_ge_rule():
    """规则高 + LLM 高 → both，取 LLM 等级。"""
    clauses = [_clause("c1")]
    hits = [RuleHit(clause_id="c1", rule_id="R01", level="高", risk_type="押金")]
    llm = [LlmJudgement(clause_id="c1", risk_level="高", risk_type="押金")]
    items = fuse(clauses, hits, llm)
    assert len(items) == 1
    assert items[0].source == "both"
    assert items[0].final_level == "高"
    assert not items[0].needs_review


def test_rule_override_when_llm_lt_rule():
    """规则高 + LLM 低 → rule_override，等级取规则（高），需复核。"""
    clauses = [_clause("c1")]
    hits = [RuleHit(clause_id="c1", rule_id="R01", level="高", risk_type="押金")]
    llm = [LlmJudgement(clause_id="c1", risk_level="低", risk_type="押金")]
    items = fuse(clauses, hits, llm)
    assert items[0].source == "rule_override"
    assert items[0].final_level == "高"
    assert items[0].needs_review


def test_llm_only_without_rule():
    """无规则命中 + LLM 中 → llm_only。"""
    clauses = [_clause("c1")]
    llm = [LlmJudgement(clause_id="c1", risk_level="中", risk_type="违约")]
    items = fuse(clauses, [], llm)
    assert items[0].source == "llm_only"
    assert items[0].final_level == "中"


def test_rule_only_when_llm_missing():
    """规则命中 + LLM 判定缺失（降级）→ rule_only。"""
    clauses = [_clause("c1")]
    hits = [RuleHit(clause_id="c1", rule_id="R02", level="中", risk_type="押金")]
    items = fuse(clauses, hits, [])
    assert items[0].source == "rule_only"
    assert items[0].final_level == "中"


def test_no_risk_llm_only_is_dropped():
    """LLM 判无风险且无规则命中 → 不进风险列表。"""
    clauses = [_clause("c1")]
    llm = [LlmJudgement(clause_id="c1", risk_level="无")]
    items = fuse(clauses, [], llm)
    assert items == []


def test_risk_type_fallback_to_rule():
    """LLM 未给出类型时，用规则命中的 risk_type 兜底。"""
    clauses = [_clause("c1")]
    hits = [RuleHit(clause_id="c1", rule_id="R01", level="高", risk_type="押金")]
    llm = [LlmJudgement(clause_id="c1", risk_level="高", risk_type="")]
    items = fuse(clauses, hits, llm)
    assert items[0].risk_type == "押金"

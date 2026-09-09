"""评分与雷达测试（FR-09）。"""

from api.engine.scoring import score_report
from api.schemas import RiskItem


def _item(level, risk_type, cid="c1") -> RiskItem:
    return RiskItem(clause_id=cid, clause_text="条款", final_level=level, risk_type=risk_type)


def test_score_deterministic():
    items = [_item("高", "押金"), _item("中", "违约金"), _item("低", "维修")]
    a = score_report("f1", "rent", items, [], clause_count=10)
    b = score_report("f1", "rent", items, [], clause_count=10)
    assert a.total_score == b.total_score
    assert a.radar == b.radar


def test_high_risk_thresholds():
    # 4 条高风险 = -80 → 20 分，落入"高"档
    items = [
        _item("高", "押金", "c1"),
        _item("高", "解约", "c2"),
        _item("高", "违约金", "c3"),
        _item("高", "维修", "c4"),
    ]
    r = score_report("f1", "rent", items, [], clause_count=10)
    assert r.total_score == 20
    assert r.overall_level == "高"


def test_radar_dimensions_rent():
    items = [_item("高", "押金")]
    r = score_report("f1", "rent", items, [], clause_count=10)
    assert r.radar_dimensions == ["押金", "解约", "维修", "违约", "隐私"]
    assert r.radar["押金"] == 80.0  # 100 - 20


def test_radar_dimensions_labor():
    items = [_item("高", "试用期")]
    r = score_report("f1", "labor", items, [], clause_count=10)
    assert r.radar_dimensions == ["工资", "试用期", "社保", "竞业限制", "违约金"]
    assert r.radar["试用期"] == 80.0


def test_unmapped_type_excluded_from_radar():
    """无法归类的风险类型不计入雷达，但仍计入总分。"""
    items = [_item("中", "其他")]
    r = score_report("f1", "rent", items, [], clause_count=10)
    assert r.total_score == 92  # 100 - 8
    # 五个维度都未扣分
    assert all(v == 100.0 for v in r.radar.values())


def test_risk_counts():
    items = [_item("高", "押金"), _item("高", "押金"), _item("中", "维修")]
    r = score_report("f1", "rent", items, [], clause_count=10)
    assert r.risk_counts == {"高": 2, "中": 1, "低": 0, "无": 0}


def test_overall_level_mid():
    # 1 条高风险 = -20 → 80 分，落入"低"档；3 条高风险 = -60 → 40 分，落入"中"档
    items = [_item("高", "押金"), _item("高", "解约"), _item("高", "违约金")]
    r = score_report("f1", "rent", items, [], clause_count=10)
    assert r.total_score == 40
    assert r.overall_level == "中"


def test_overall_level_low():
    items = [_item("高", "押金")]
    r = score_report("f1", "rent", items, [], clause_count=10)
    assert r.total_score == 80
    assert r.overall_level == "低"


def test_injection_flagged():
    items = [_item("高", "押金"), _item("中", "违约金", "c2")]
    items[1].injection_suspected = True
    r = score_report("f1", "rent", items, [], clause_count=10)
    assert r.injection_flagged is True

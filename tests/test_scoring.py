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
    # 4 条高风险：累计扣分 80 → 饱和后 100*60/(80+60) ≈ 43 分，落入"中"档（濒危）
    items = [
        _item("高", "押金", "c1"),
        _item("高", "解约", "c2"),
        _item("高", "违约金", "c3"),
        _item("高", "维修", "c4"),
    ]
    r = score_report("f1", "rent", items, [], clause_count=10)
    assert r.total_score == 43
    assert r.overall_level == "中"


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
    assert r.total_score == 88  # 100*60/(8+60) ≈ 88.24
    # 五个维度都未扣分
    assert all(v == 100.0 for v in r.radar.values())


def test_risk_counts():
    items = [_item("高", "押金"), _item("高", "押金"), _item("中", "维修")]
    r = score_report("f1", "rent", items, [], clause_count=10)
    assert r.risk_counts == {"高": 2, "中": 1, "低": 0, "无": 0}


def test_overall_level_mid():
    # 3 条高风险：累计扣分 60 = K → 饱和后恰好 50 分，落入"中"档
    items = [_item("高", "押金"), _item("高", "解约"), _item("高", "违约金")]
    r = score_report("f1", "rent", items, [], clause_count=10)
    assert r.total_score == 50
    assert r.overall_level == "中"


def test_overall_level_low():
    # 1 条高风险：累计扣分 20 → 100*60/(20+60) = 75 分，落入"低"档
    items = [_item("高", "押金")]
    r = score_report("f1", "rent", items, [], clause_count=10)
    assert r.total_score == 75
    assert r.overall_level == "低"


def test_total_score_never_zero():
    """R-SCORE-01：风险再密集也不归零，否则不同严重程度的合同无法区分。"""
    items = [_item("高", "押金", f"c{i}") for i in range(40)]
    r = score_report("f1", "rent", items, [], clause_count=40)
    assert r.total_score >= 1
    assert r.overall_level == "高"


def test_total_score_monotonic_decreasing():
    """扣分越多总分越低（饱和不能破坏单调性）。"""
    scores = []
    for n in range(0, 9):
        items = [_item("高", "押金", f"c{i}") for i in range(n)]
        scores.append(score_report("f1", "rent", items, [], clause_count=20).total_score)
    assert scores == sorted(scores, reverse=True)
    assert len(set(scores)) == len(scores)  # 严格递减，无并列平台期


def test_total_score_distinguishes_severity():
    """重风险区间仍要有区分度：3 条高 vs 6 条高不能同分。"""
    light = score_report("f1", "rent", [_item("高", "押金", f"a{i}") for i in range(3)], [], clause_count=20)
    heavy = score_report("f1", "rent", [_item("高", "押金", f"b{i}") for i in range(6)], [], clause_count=20)
    assert light.total_score > heavy.total_score
    assert light.total_score - heavy.total_score >= 5


def test_injection_flagged():
    items = [_item("高", "押金"), _item("中", "违约金", "c2")]
    items[1].injection_suspected = True
    r = score_report("f1", "rent", items, [], clause_count=10)
    assert r.injection_flagged is True

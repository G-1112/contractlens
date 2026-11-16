"""风险评分与雷达（FR-09）。

- 合同总分 0-100，越高越安全；同一合同重复分析得分一致（确定性）。
- 五维雷达随合同类型变化（维度名见 _DIMENSIONS）。
- 评分规则在此文档化，保证可复现。
"""

from __future__ import annotations

from ..schemas import AnalysisReport, RiskItem, RiskLevel

# 风险等级扣分
_LEVEL_PENALTY: dict[RiskLevel, float] = {"高": 20.0, "中": 8.0, "低": 2.0, "无": 0.0}

# 每个合同类型的五个雷达维度（顺序即前端展示顺序）
_DIMENSIONS: dict[str, list[str]] = {
    "rent": ["押金", "解约", "维修", "违约", "隐私"],
    "labor": ["工资", "试用期", "社保", "竞业限制", "违约金"],
    "intern": ["报酬", "安全", "期限", "知识产权", "违约金"],
}

# risk_type 关键词 → 维度（先精确匹配 risk_type，再回退到正文关键词）
_TYPE_TO_DIM: dict[str, dict[str, str]] = {
    "rent": {
        "押金": "押金",
        "涨租": "解约",
        "解约": "解约",
        "维修": "维修",
        "违约": "违约",
        "隐私": "隐私",
    },
    "labor": {
        "工资": "工资",
        "试用期": "试用期",
        "社保": "社保",
        "竞业": "竞业限制",
        "保密": "竞业限制",
        "违约": "违约金",
    },
    "intern": {
        "报酬": "报酬",
        "押金": "报酬",
        "保险": "安全",
        "安全": "安全",
        "工伤": "安全",
        "期限": "期限",
        "工时": "期限",
        "知识产权": "知识产权",
        "保密": "知识产权",
        "违约": "违约金",
    },
}

# 正文兜底关键词 → 维度（用于 risk_type 为"其他"时的归类）
_TEXT_HINTS: dict[str, dict[str, str]] = {
    "rent": {
        "押金": "押金", "涨租": "解约", "解除": "解约", "维修": "维修",
        "修缮": "维修", "违约": "违约", "滞纳": "违约", "隐私": "隐私", "进入": "隐私",
    },
    "labor": {
        "工资": "工资", "报酬": "工资", "加班": "工资", "罚款": "工资",
        "试用期": "试用期", "社保": "社保", "保险": "社保", "公积金": "社保",
        "竞业": "竞业限制", "保密": "竞业限制", "违约": "违约金", "赔偿": "违约金",
    },
    "intern": {
        "报酬": "报酬", "工资": "报酬", "补贴": "报酬", "押金": "报酬",
        "保险": "安全", "工伤": "安全", "安全": "安全",
        "期限": "期限", "工时": "期限", "实习期": "期限", "休假": "期限",
        "知识产权": "知识产权", "成果": "知识产权", "保密": "知识产权",
        "违约": "违约金", "赔偿": "违约金",
    },
}


def _dim_of(contract_type: str, item: RiskItem) -> str | None:
    """把风险条目归入雷达维度；无法归类返回 None（不计入雷达，但仍计入总分）。"""
    mapping = _TYPE_TO_DIM.get(contract_type, {})
    # 1) 精确匹配 risk_type
    for kw, dim in mapping.items():
        if kw in item.risk_type:
            return dim
    # 2) 正文兜底
    haystack = f"{item.risk_type} {item.explain} {item.clause_text}"
    for kw, dim in _TEXT_HINTS.get(contract_type, {}).items():
        if kw in haystack:
            return dim
    return None


def score_report(
    file_id: str,
    contract_type: str,
    items: list[RiskItem],
    talk_scripts: list[str],
    clause_count: int,
    truncated: bool = False,
) -> AnalysisReport:
    """由风险条目计算总分 / 等级 / 雷达。"""
    dims = _DIMENSIONS.get(contract_type, _DIMENSIONS["rent"])
    total = 100.0
    dim_penalty = {d: 0.0 for d in dims}

    for item in items:
        if item.final_level == "无":
            continue
        penalty = _LEVEL_PENALTY[item.final_level]
        total -= penalty
        dim = _dim_of(contract_type, item)
        if dim is not None:
            dim_penalty[dim] += penalty

    total = int(max(0, min(100, round(total))))
    overall: RiskLevel = "低"
    if total < 40:
        overall = "高"
    elif total < 70:
        overall = "中"

    radar = {d: round(max(0.0, min(100.0, 100.0 - dim_penalty[d])), 1) for d in dims}

    risk_counts = {"高": 0, "中": 0, "低": 0, "无": 0}
    for item in items:
        risk_counts[item.final_level] = risk_counts.get(item.final_level, 0) + 1

    injection_flagged = any(getattr(item, "injection_suspected", False) for item in items)

    return AnalysisReport(
        file_id=file_id,
        contract_type=contract_type,  # type: ignore[arg-type]
        total_score=total,
        overall_level=overall,
        radar=radar,
        radar_dimensions=dims,
        items=items,
        talk_scripts=talk_scripts,
        injection_flagged=injection_flagged,
        clause_count=clause_count,
        truncated=truncated,
        risk_counts=risk_counts,
    )

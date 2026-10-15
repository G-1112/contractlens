"""风险评分与雷达（FR-09）。

- 合同总分 0-100，越高越安全；同一合同重复分析得分一致（确定性）。
- 五维雷达随合同类型变化（维度名见 _DIMENSIONS）。
- 评分规则在此文档化，保证可复现。
"""

from __future__ import annotations

from ..schemas import AnalysisReport, RiskItem, RiskLevel

# 风险等级扣分
_LEVEL_PENALTY: dict[RiskLevel, float] = {"高": 20.0, "中": 8.0, "低": 2.0, "无": 0.0}

# 总分饱和系数（R-SCORE-01）：扣分累计到 K 时总分恰好为 50
_TOTAL_SATURATION_K = 60.0

# 每个合同类型的五个雷达维度（顺序即前端展示顺序）
_DIMENSIONS: dict[str, list[str]] = {
    "rent": ["押金", "解约", "维修", "违约", "隐私"],
    "labor": ["工资", "试用期", "社保", "竞业限制", "违约金"],
    "intern": ["报酬", "安全", "期限", "知识产权", "违约金"],
    "loan": ["利率", "逾期违约", "提前到期", "信息授权", "责任免除"],
    "guarantee": ["保证方式", "保证范围", "保证期间", "主合同变更", "追偿抗辩"],
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
    "loan": {
        "利率": "利率",
        "违约金": "逾期违约",
        "解约": "提前到期",
        "隐私": "信息授权",
    },
    "guarantee": {
        "保证方式": "保证方式",
        "保证范围": "保证范围",
        "保证期间": "保证期间",
        "主合同变更": "主合同变更",
        "追偿": "追偿抗辩",
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
    "loan": {
        "利率": "利率", "利息": "利率", "砍头息": "利率", "复利": "利率",
        "手续费": "利率", "服务费": "利率",
        "逾期": "逾期违约", "滞纳": "逾期违约", "违约金": "逾期违约",
        "提前到期": "提前到期", "提前偿还": "提前到期", "解约": "提前到期",
        "征信": "信息授权", "通讯录": "信息授权", "披露": "信息授权",
        "授权": "信息授权", "隐私": "信息授权",
        "免责": "责任免除", "不承担": "责任免除", "放弃抗辩": "责任免除",
    },
    "guarantee": {
        "连带": "保证方式", "一般保证": "保证方式", "先诉": "保证方式",
        "范围": "保证范围", "无限": "保证范围", "本金": "保证范围",
        "期间": "保证期间", "期限": "保证期间",
        "变更": "主合同变更", "转让": "主合同变更",
        "追偿": "追偿抗辩", "抗辩": "追偿抗辩", "撤销": "追偿抗辩",
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


def saturate_total(raw_penalty: float) -> int:
    """把累计扣分映射为 0-100 的合同总分（R-SCORE-01）。

    为什么不用线性（100 - P）：高风险每条扣 20 分，命中 5 条即扣满 100 归零。
    实测五个内置样本全部落到 0-22 分区间、等级一律「高」，借款/保证两型直接
    归零 —— 演示时任何一份合同都显示「高风险 0 分」，评分体系彻底失去区分度。

    改用双曲饱和：

        total = 100 * K / (P + K)

    - P=0 → 100；P=K(=60) → 50；P→∞ → 0⁺
    - 严格单调递减且永不取到 0，风险再多的合同也能相互比较
    - 保持确定性（同一份合同重复分析得分一致）

    代价：中低风险区间的分数比线性更「宽容」（如 1 条高风险 75 分而非 80），
    换取的是重风险区间的区分度。等级阈值（<40 高 / <70 中）保持不变。
    """
    total = 100.0 * _TOTAL_SATURATION_K / (raw_penalty + _TOTAL_SATURATION_K)
    return int(max(1, min(100, round(total))))


def score_report(
    file_id: str,
    contract_type: str,
    items: list[RiskItem],
    talk_scripts: list[str],
    clause_count: int,
) -> AnalysisReport:
    """由风险条目计算总分 / 等级 / 雷达。

    总分走饱和映射（见 saturate_total），雷达维度仍用线性扣分：
    雷达要回答「问题集中在哪个维度」，线性对单维度堆积更敏感。
    """
    dims = _DIMENSIONS.get(contract_type, _DIMENSIONS["rent"])
    raw_penalty = 0.0
    dim_penalty = {d: 0.0 for d in dims}

    for item in items:
        if item.final_level == "无":
            continue
        penalty = _LEVEL_PENALTY[item.final_level]
        raw_penalty += penalty
        dim = _dim_of(contract_type, item)
        if dim is not None:
            dim_penalty[dim] += penalty

    total = saturate_total(raw_penalty)
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
        risk_counts=risk_counts,
    )

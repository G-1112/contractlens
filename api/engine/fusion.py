"""双引擎融合定级（FR-07）。

融合策略（规则命中即抬高等级下限，AI 判断不冒充权威）：

    if rule_hit and llm_level >= rule_level:
        final, source = llm_level,  "both"
    elif rule_hit and llm_level < rule_level:
        final, source = rule_level, "rule_override"   # 标记"需复核"
    else:
        final, source = llm_level,  "llm_only"

验收：20 条规则与 LLM 冲突样本，融合结果 100% 符合上表策略。
"""

from __future__ import annotations

from ..schemas import Clause, LlmJudgement, RiskItem, RiskLevel, RuleHit

# 等级排序：用于"抬高下限"比较
_LEVEL_RANK: dict[RiskLevel, int] = {"无": 0, "低": 1, "中": 2, "高": 3}


def _max_level(a: RiskLevel, b: RiskLevel) -> RiskLevel:
    return a if _LEVEL_RANK[a] >= _LEVEL_RANK[b] else b


def _compare(llm_level: RiskLevel, rule_level: RiskLevel) -> int:
    """返回 llm 相对 rule 的等级差：>0 高 / =0 相等 / <0 低。"""
    return _LEVEL_RANK[llm_level] - _LEVEL_RANK[rule_level]


class FusionEngine:
    """把规则命中与 LLM 判定融合为最终风险条目。"""

    def fuse(
        self,
        clauses: list[Clause],
        rule_hits: list[RuleHit],
        llm_judgements: list[LlmJudgement | None],
        regulation_refs: dict[str, list[str]] | None = None,
    ) -> list[RiskItem]:
        clause_map = {c.id: c for c in clauses}
        refs = regulation_refs or {}

        # 按条款聚合规则命中：取最高等级 + 所有 rule_id
        rule_by_clause: dict[str, list[RuleHit]] = {}
        for h in rule_hits:
            rule_by_clause.setdefault(h.clause_id, []).append(h)

        # 第十二轮修复：LLM 判定缺失的条款以 None 占位（pipeline 在 LLM/检索
        # 失败时返回 None），None 不进入 llm_by_clause —— 规则命中条目由此走
        # 真正的 rule_only 分支（此前 None 永远不会出现，rule_only 是死代码，
        # LLM 整体失败时所有规则命中被误标为 rule_override + 误导文案）。
        llm_by_clause: dict[str, LlmJudgement] = {j.clause_id: j for j in llm_judgements if j is not None}

        items: list[RiskItem] = []
        # 参与过任一引擎判定的条款 id 并集
        all_ids = set(rule_by_clause) | set(llm_by_clause)

        for clause_id in all_ids:
            clause = clause_map.get(clause_id)
            clause_text = clause.text if clause else ""
            page = clause.page if clause else 1

            hits = rule_by_clause.get(clause_id, [])
            llm = llm_by_clause.get(clause_id)

            rule_level: RiskLevel = "无"
            if hits:
                rule_level = max((h.level for h in hits), key=lambda x: _LEVEL_RANK[x])  # type: ignore[arg-type]

            llm_level: RiskLevel = llm.risk_level if llm else "无"

            # ---- 融合策略 ----
            if hits and llm is not None and _compare(llm_level, rule_level) >= 0:
                final_level, source = llm_level, "both"
            elif hits and llm is not None and _compare(llm_level, rule_level) < 0:
                final_level, source = rule_level, "rule_override"
            elif hits:
                # 规则命中但 LLM 判定缺失（如 LLM 失败降级）
                final_level, source = rule_level, "rule_only"
            else:
                final_level, source = llm_level, "llm_only"

            # 无风险且非规则命中 → 不进入风险列表（保持结果干净）
            if final_level == "无" and source in ("llm_only",):
                continue

            needs_review = source == "rule_override"
            # 条款文本疑似注入 → 强制提示复核（与 rule_override 同等级提示）
            injection_suspected = bool(llm.injection_suspected) if llm else False
            if injection_suspected:
                needs_review = True

            # 解释文本来源：规则命中优先展示规则解释，LLM 补充语义
            explain = llm.plain_explain if llm and llm.plain_explain else ""
            suggest = llm.suggest if llm else ""
            rewrite = llm.rewrite if llm else ""
            risk_type = llm.risk_type if llm else ""
            if hits:
                # LLM 未给出类型时（mock/降级），用规则命中的 risk_type 兜底
                if not risk_type:
                    risk_type = hits[0].risk_type
                rule_explain = "；".join(h.explanation for h in hits if h.explanation)
                if source in ("rule_override", "rule_only"):
                    explain = rule_explain or explain
                if source == "rule_override":
                    suggest = suggest or "该条款被规则引擎判定为更高风险，建议人工复核。"
            # rule_only 时给一个基于规则的通用建议
            if source == "rule_only" and not suggest:
                suggest = "建议与对方协商修改该条款，明确双方权利义务。"

            items.append(
                RiskItem(
                    clause_id=clause_id,
                    clause_text=clause_text,
                    page=page,
                    final_level=final_level,
                    source=source,
                    needs_review=needs_review,
                    injection_suspected=injection_suspected,
                    risk_type=risk_type,
                    explain=explain,
                    suggest=suggest,
                    rewrite=rewrite,
                    regulation_refs=refs.get(clause_id, []),
                    rule_ids=[h.rule_id for h in hits],
                )
            )

        # 高风险在前，其次按页/条款顺序
        items.sort(key=lambda it: (-_LEVEL_RANK[it.final_level], it.page, it.clause_id))
        return items


def fuse(
    clauses: list[Clause],
    rule_hits: list[RuleHit],
    llm_judgements: list[LlmJudgement],
    regulation_refs: dict[str, list[str]] | None = None,
) -> list[RiskItem]:
    """便捷函数。"""
    return FusionEngine().fuse(clauses, rule_hits, llm_judgements, regulation_refs)

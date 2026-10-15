"""规则引擎（FR-05）。

三类确定性检测，零 LLM 依赖：
1. keyword   关键词匹配（含否定检测）
2. threshold 数值阈值（提取数字与单位后比较）
3. missing   缺失检测（该写没写，文档级）

规则库以 YAML 维护（api/rules/*.yaml），本引擎只负责解释执行。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from ..schemas import Clause, RiskLevel, RuleHit

# 中文数字 → 数值（支持到"千"级，覆盖合同常见写法）
_CN_DIGIT = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "百": 100, "千": 1000,
}

# 条款序号后缀（第N条/款/项/章/节）：阈值规则提取数值时须排除（见 _extract_number_unit_pairs）
_CLAUSE_NUM_SUFFIX = "条款项章节"

# 数值 + 单位 联合提取：用于 threshold 规则判断
_NUM_UNIT = re.compile(
    r"(?P<num>\d+(?:\.\d+)?|[一二两三四五六七八九十百千]+)\s*(?P<unit>%|％|倍|个月|月|元|块|天|日|年)?"
)


class Rule:
    """单条规则（由 YAML 反序列化）。"""

    def __init__(self, raw: dict) -> None:
        self.id: str = raw["id"]
        self.level: RiskLevel = raw.get("level", "中")
        self.risk_type: str = raw.get("risk_type", "其他")
        self.detect: str = raw.get("detect", "keyword")
        self.keywords: list[str] = raw.get("keywords", [])
        self.negate: list[str] = raw.get("negate", [])
        self.must_contain: list[str] = raw.get("must_contain", [])
        self.unit: str = raw.get("unit", "")
        self.operator: str = raw.get("operator", "gt")
        self.threshold: float = float(raw.get("threshold", 0))
        self.ratio: bool = raw.get("ratio", False)
        self.explanation: str = raw.get("explanation", "")
        self.anchor: str = raw.get("anchor", "")  # threshold 规则中数字所属的词

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Rule {self.id} {self.detect} {self.level}>"


class RuleEngine:
    """加载规则库并执行三类检测。"""

    def __init__(self, rules_dir: str | Path | None = None) -> None:
        if rules_dir is None:
            from ..config import RULES_DIR

            rules_dir = RULES_DIR
        self.rules_dir = Path(rules_dir)
        self._rules_by_type: dict[str, list[Rule]] = {}
        self._all_rules: list[Rule] = []

    # ------------------------------------------------------------------
    def load(self, contract_type: str) -> "RuleEngine":
        """按合同类型加载规则文件（rent/labor/intern）。"""
        path = self.rules_dir / f"{contract_type}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"规则文件不存在：{path}")
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        rules = [Rule(r) for r in data.get("rules", [])]
        self._all_rules = rules
        self._rules_by_type = {}
        for r in rules:
            self._rules_by_type.setdefault(r.detect, []).append(r)
        return self

    @property
    def all_rules(self) -> list[Rule]:
        return self._all_rules

    # ------------------------------------------------------------------
    def match_clauses(self, clauses: list[Clause]) -> list[RuleHit]:
        """对每个条款执行 keyword / threshold 检测。"""
        hits: list[RuleHit] = []
        for clause in clauses:
            for rule in self._rules_by_type.get("keyword", []):
                if self._match_keyword(rule, clause.text):
                    hits.append(
                        RuleHit(
                            clause_id=clause.id,
                            rule_id=rule.id,
                            level=rule.level,
                            risk_type=rule.risk_type,
                            evidence=self._find_evidence(rule, clause.text),
                            explanation=rule.explanation,
                        )
                    )
            for rule in self._rules_by_type.get("threshold", []):
                if self._match_threshold(rule, clause.text):
                    hits.append(
                        RuleHit(
                            clause_id=clause.id,
                            rule_id=rule.id,
                            level=rule.level,
                            risk_type=rule.risk_type,
                            evidence=self._find_evidence(rule, clause.text),
                            explanation=rule.explanation,
                        )
                    )
        return hits

    def match_document(self, clauses: list[Clause]) -> list[RuleHit]:
        """文档级缺失检测：该写没写。clause_id 用规则 id 标识。"""
        full_text = "\n".join(c.text for c in clauses)
        hits: list[RuleHit] = []
        for rule in self._rules_by_type.get("missing", []):
            if not any(term in full_text for term in rule.must_contain):
                hits.append(
                    RuleHit(
                        clause_id=f"missing-{rule.id}",
                        rule_id=rule.id,
                        level=rule.level,
                        risk_type=rule.risk_type,
                        evidence="",
                        explanation=rule.explanation,
                    )
                )
        return hits

    # ------------------------------------------------------------------
    @staticmethod
    def _match_keyword(rule: Rule, text: str) -> bool:
        if not any(k in text for k in rule.keywords):
            return False
        # 否定检测：命中关键词但出现排除词 → 不触发
        if rule.negate and any(n in text for n in rule.negate):
            return False
        return True

    @staticmethod
    def _find_evidence(rule: Rule, text: str) -> str:
        """返回命中的原文片段（用于 UI 标注"规则命中"证据）。"""
        for k in rule.keywords:
            idx = text.find(k)
            if idx >= 0:
                start = max(0, idx - 8)
                end = min(len(text), idx + len(k) + 20)
                return text[start:end]
        return text[:60]

    # ------------------------------------------------------------------
    def _match_threshold(self, rule: Rule, text: str) -> bool:
        """数值阈值检测。"""
        anchor_hit = rule.anchor in text if rule.anchor else True
        if not anchor_hit:
            return False
        pairs = self._extract_number_unit_pairs(text)
        for num, unit in pairs:
            # 单位过滤：规则要求特定单位时，仅比较带该单位（或无单位）的数值
            if rule.unit and unit and unit != rule.unit:
                continue
            value = num if not rule.ratio else num  # ratio 值已在提取阶段归一化
            if self._compare(value, rule.operator, rule.threshold):
                return True
        return False

    @staticmethod
    def _compare(a: float, op: str, b: float) -> bool:
        if op == "gt":
            return a > b
        if op == "ge":
            return a >= b
        if op == "lt":
            return a < b
        if op == "le":
            return a <= b
        return a >= b

    # ------------------------------------------------------------------
    @classmethod
    def _extract_number_unit_pairs(cls, text: str) -> list[tuple[float, str]]:
        """提取 (数值, 单位) 对，中文数字自动换算。

        - "押金为三个月租金" → (3.0, "个月")
        - "违约金 200%" → (200.0, "%")
        - "赔付 5000 元" → (5000.0, "元")
        """
        pairs: list[tuple[float, str]] = []
        for m in _NUM_UNIT.finditer(text):
            # 第十二轮修复（实证 P1）：条款序号（第N条/款/项/章/节）不是业务数值。
            # 原实现把「第三条」提取为 (3.0, "")，在「单位不限/无单位数值也参与比较」
            # 的口径下命中阈值规则 —— 「第三条 押金为一个月租金」被误报
            # 「押金超过两个月」（3 > 2），第三条及以后条款大面积误报。
            prev_ch = text[m.start() - 1] if m.start() > 0 else ""
            next_ch = text[m.end()] if m.end() < len(text) else ""
            if prev_ch == "第" and next_ch in _CLAUSE_NUM_SUFFIX:
                continue
            raw = m.group("num")
            unit = m.group("unit") or ""
            num = cls._to_number(raw)
            if num is None:
                continue
            pairs.append((num, unit))
        return pairs

    @staticmethod
    def _to_number(s: str) -> float | None:
        """中文数字/阿拉伯数字 → float。"""
        s = s.strip()
        if not s:
            return None
        if re.fullmatch(r"\d+(?:\.\d+)?", s):
            return float(s)
        # 中文数字换算（只支持常见整数写法）
        if re.fullmatch(r"[零〇一二两三四五六七八九十百千]+", s):
            total = 0
            section = 0
            num = 0
            for ch in s:
                if ch in ("百", "千"):
                    section = (section or 1) * _CN_DIGIT[ch]
                    total += section
                    section = 0
                elif ch == "十":
                    section = (section or 1) * 10
                else:
                    num = _CN_DIGIT[ch]
                    section += num
            total += section
            return float(total)
        return None

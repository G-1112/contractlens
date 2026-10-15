"""构建条款级标注测试集（合成样例）。

⚠️ 数据性质说明（必须随结果一并披露）：
本测试集是**程序合成样例**，不是真实合同语料。构造方式为「从规则库抽取关键词 →
套用固定句式模板 → 随机组合成篇」，因此标注（ground truth）由构造过程天然确定，
不含人工判读误差。用途是规则引擎的回归度量与指标测算，**不代表真实合同的
条款分布与风险分布**，PPT/报告中引用时必须标注「合成样例」。

产物：
  data/evalset/contracts/*.docx   50 份（5 类 × 10 份）
  data/evalset/labels.json        每份的条款级标注

用法：python scripts/build_evalset.py
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import yaml

try:
    from docx import Document
except ImportError:  # pragma: no cover
    raise SystemExit("请先安装 python-docx：pip install python-docx")

ROOT = Path(__file__).resolve().parent.parent
RULES_DIR = ROOT / "api" / "rules"
OUT = ROOT / "data" / "evalset"
CONTRACTS = OUT / "contracts"

PER_TYPE = 10  # 每类合同份数
SEED = 20261015

# 中性条款池：不含任何规则关键词，也不含 mock 启发式的风险词
# （"未经/另行/书面同意/提前/通知"等会触发 mock 判定，故一律避开）
NEUTRAL_POOL: list[str] = [
    "本合同自双方签字盖章之日起生效。",
    "本合同一式两份，甲乙双方各执一份，具有同等效力。",
    "本合同的订立、效力、解释及争议解决均适用中华人民共和国法律。",
    "双方确认已充分阅读并理解本合同全部内容。",
    "本合同未尽事宜由双方友好协商解决。",
    "双方应当按照约定全面履行各自义务。",
    "本合同项下的通知以书面形式送达对方预留地址。",
    "本合同附件为本合同不可分割的组成部分。",
]

# 风险条款句式模板：{kw} 替换为规则关键词
RISK_TEMPLATES: list[str] = [
    "双方约定：{kw}，乙方应予遵守。",
    "甲方主张{kw}，乙方不得提出异议。",
    "本合同项下适用{kw}，具体标准由甲方确定。",
    "如发生{kw}，乙方应承担相应后果。",
]


def load_keyword_rules(ctype: str) -> list[dict]:
    """取指定合同类型的 keyword 类规则（含明确关键词，可构造正例）。"""
    data = yaml.safe_load((RULES_DIR / f"{ctype}.yaml").read_text(encoding="utf-8"))
    return [r for r in data.get("rules", []) if r.get("detect") == "keyword" and r.get("keywords")]


def build() -> None:
    rng = random.Random(SEED)
    CONTRACTS.mkdir(parents=True, exist_ok=True)

    labels: dict[str, dict] = {}
    for ctype in ("rent", "labor", "intern", "loan", "guarantee"):
        rules = load_keyword_rules(ctype)
        if not rules:
            continue
        for i in range(PER_TYPE):
            chosen = rng.sample(rules, k=min(len(rules), rng.randint(3, 8)))
            n_neutral = rng.randint(2, 4)

            clauses: list[str] = []
            gold: list[dict] = []
            # 打散风险条款与中性条款的顺序（避免全篇前段都是风险项）
            slots: list[tuple[str, dict]] = []
            for r in chosen:
                kw = r["keywords"][0]
                text = rng.choice(RISK_TEMPLATES).format(kw=kw)
                slots.append((text, {"level": r.get("level", "中"), "risk_type": r.get("risk_type", "其他"), "rule_id": r["id"]}))
            for _ in range(n_neutral):
                slots.append((rng.choice(NEUTRAL_POOL), None))
            rng.shuffle(slots)

            for idx, (text, meta) in enumerate(slots, start=1):
                clauses.append(f"第{idx}条 {text}")
                gold.append(
                    {
                        "index": idx - 1,
                        "text": f"第{idx}条 {text}",
                        "is_risk": meta is not None,
                        "level": meta["level"] if meta else "无",
                        "risk_type": meta["risk_type"] if meta else "",
                        "rule_id": meta["rule_id"] if meta else "",
                    }
                )

            doc = Document()
            for line in clauses:
                doc.add_paragraph(line)
            filename = f"{ctype}_{i + 1:02d}.docx"
            doc.save(str(CONTRACTS / filename))

            labels[filename] = {
                "contract_type": ctype,
                "clause_count": len(clauses),
                "risk_clauses": sum(1 for g in gold if g["is_risk"]),
                "clauses": gold,
            }

    (OUT / "labels.json").write_text(
        json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    total = len(labels)
    total_risk = sum(v["risk_clauses"] for v in labels.values())
    total_clauses = sum(v["clause_count"] for v in labels.values())
    print(f"生成 {total} 份合同，共 {total_clauses} 条条款，其中标注风险条款 {total_risk} 条")
    print(f"输出：{CONTRACTS}")
    print(f"标注：{OUT / 'labels.json'}")
    print("\n⚠️ 本测试集为程序合成样例，非真实合同语料，引用时须标注来源与性质。")


if __name__ == "__main__":
    build()

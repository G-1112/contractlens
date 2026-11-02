"""真实集分层抽样 → data/evalset_real/labels.json（待标注模板）。

抽样口径（2026-11-02 定）：
  1) 全量纳入系统在**纯规则模式**下命中的条款（高/中/低）——这些是 FN/TP 的关键；
  2) 每份合同再从**未命中**条款中随机抽 5 条（固定种子）——用于估计 FP 与召回；
  3) 标注时**不显示系统判定**（盲标），避免标注被系统结论带偏。

为什么抽样：20 份合同共 1131 条，全标约 2 小时；分层抽样约 300 条，
可在 30–40 分钟内完成，且能同时估计 TP/FP/FN。

用法（需在 LLM_API_KEY 为空的环境下运行，保证是纯规则口径）：
  LLM_API_KEY= python scripts/sample_real_labels.py
  python scripts/sample_real_labels.py --per-contract 5 --seed 42
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.pipeline import parse_file, run_analysis  # noqa: E402

REAL_DIR = ROOT / "data" / "evalset_real"
CONTRACTS = REAL_DIR / "contracts"
LABELS = REAL_DIR / "labels.json"


def ctype_of(name: str) -> str:
    m = re.search(r"real_(rent|labor|intern|loan|gua)", name)
    return {"rent": "rent", "labor": "labor", "intern": "intern",
            "loan": "loan", "gua": "guarantee"}.get(m.group(1) if m else "", "rent")


def index_of(clause_id: str) -> int | None:
    m = re.match(r"c-\d+-(\d+)$", clause_id or "")
    return int(m.group(1)) - 1 if m else None


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-contract", type=int, default=5, help="每份合同未命中条款随机抽样数")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=LABELS)
    ap.add_argument("--only-new", action="store_true",
                    help="增量模式：保留 labels.json 中已有合同（含已标注内容），只为新合同抽样")
    args = ap.parse_args()

    if os.environ.get("LLM_API_KEY"):
        print("[!] LLM_API_KEY 非空：本次将走真实 LLM，抽样口径不再是纯规则。")
        print("    建议：LLM_API_KEY= python scripts/sample_real_labels.py")

    files = sorted(CONTRACTS.glob("*.docx"))
    if not files:
        print(f"[x] {CONTRACTS} 下没有 docx")
        return 1

    labels: dict[str, dict] = {}
    if args.only_new and args.out.exists():
        labels = json.loads(args.out.read_text(encoding="utf-8"))
        print(f"[i] 增量模式：已有 {len(labels)} 份标注，仅处理新合同")

    totals = dict(all_clauses=0, flagged=0, sampled=0)

    for f in files:
        if args.only_new and f.name in labels:
            continue
        ctype = ctype_of(f.name)
        parsed = parse_file(str(f), f.name, ctype)
        n = len(parsed.clauses)
        report = await run_analysis(parsed, ctype, f.stem, generate_scripts=False)

        flagged: set[int] = set()
        level_of: dict[int, str] = {}
        for it in report.items:
            i = index_of(it.clause_id)
            if i is None or not (0 <= i < n):
                continue
            flagged.add(i)
            level_of[i] = it.final_level

        pool = [i for i in range(n) if i not in flagged]
        rng = random.Random(f"{args.seed}-{f.name}")
        picked = sorted(rng.sample(pool, min(args.per_contract, len(pool))))
        sampled = sorted(flagged | set(picked))

        labels[f.name] = {
            "contract_type": ctype,
            "clause_count": n,
            "risk_clauses": None,          # 标注完成后回填
            "sampling": {
                "method": "stratified",
                "flagged_all": len(flagged),
                "random_unflagged": len(picked),
                "sampled": len(sampled),
                "seed": args.seed,
                "blind": True,             # 标注页不显示系统判定
                "note": "全量纳入规则命中条款 + 未命中随机抽样；标注时不可见系统判定",
            },
            "clauses": [
                {
                    "index": i,
                    "text": parsed.clauses[i].text,
                    "is_risk": None,
                    "level": None,
                    "risk_type": "",
                    "rule_id": "",
                    "annotator_note": "",
                    "sampled_by": "flagged" if i in flagged else "random",
                }
                for i in sampled
            ],
        }
        totals["all_clauses"] += n
        totals["flagged"] += len(flagged)
        totals["sampled"] += len(sampled)
        print(f"{f.name:22s} 条款{n:4d} 命中{len(flagged):3d} 抽{len(picked):2d} → 标{len(sampled):3d}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n合计：条款 {totals['all_clauses']}，命中 {totals['flagged']}，"
          f"抽样 {totals['sampled']} 条（{totals['sampled']/max(1,totals['all_clauses']):.1%}）")
    print(f"模板：{args.out}")
    print("下一步：python scripts/build_annotation_page.py 生成盲标页面")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

"""规则引擎快速归因（不带 LLM）：对标注集跑纯规则，输出 rule_id 级 FP 明细。

用于区域赛收敛迭代：定位哪些规则在保护性/中性条款上误触发（缺 polarity 判定）。
只读标注集与规则库，不调用 LLM，秒级完成，可反复跑。

用法：python scripts/rule_fp_attrib.py --split dev [--out ../_rule_fp.json]
"""

from __future__ import annotations

import argparse

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from api.engine.rule_engine import RuleEngine  # noqa: E402
from api.pipeline import parse_file  # noqa: E402


def main() -> None:

    ap = argparse.ArgumentParser()
    ap.add_argument("--evalset", type=Path, default=ROOT / "data" / "evalset_real")
    ap.add_argument("--split", default=None, help="dev/holdout/all（读 split.json）")
    ap.add_argument("--out", type=Path, default=ROOT.parent / "_rule_fp.json")
    args = ap.parse_args()

    labels = json.loads((args.evalset / "labels.json").read_text(encoding="utf-8"))
    names = list(labels)
    if args.split and args.split != "all":
        split = json.loads((args.evalset / "split.json").read_text(encoding="utf-8"))
        names = split[args.split]

    rows = []
    for fn in names:
        meta = labels[fn]
        ctype = meta["contract_type"]
        path = args.evalset / "contracts" / fn
        parsed = parse_file(path, fn, ctype)
        idx_of = {c.id: i for i, c in enumerate(parsed.clauses)}
        engine = RuleEngine().load(ctype)
        hits = engine.match_clauses(parsed.clauses)  # 条款级（missing-* 由 pipeline 另行并入，不参与条款级评测）
        pred: dict[int, dict] = {}
        for h in hits:
            i = idx_of.get(h.clause_id)
            if i is None:
                continue
            order = {"高": 3, "中": 2, "低": 1, "无": 0}
            prev = pred.get(i)
            if prev is None or order.get(h.level, 0) > order.get(prev["level"], 0):
                pred[i] = {"level": h.level, "rule_ids": h.rule_ids if hasattr(h, "rule_ids") else getattr(h, "rule_id", "")}
        for g in meta["clauses"]:
            i = g["index"]
            p = pred.get(i)
            grisk = bool(g["is_risk"])
            prisk = p is not None and p["level"] != "无"
            if prisk and not grisk:
                rows.append({
                    "contract": fn, "type": ctype, "index": i,
                    "level": p["level"], "rule_ids": p["rule_ids"],
                    "text": g["text"][:120],
                })
    args.out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    from collections import Counter
    print(f"纯规则 FP：{len(rows)} 条（{args.split or 'all'}）")
    c = Counter()
    for r in rows:
        for rid in str(r["rule_ids"]).split(","):
            if rid.strip():
                c[rid.strip()] += 1
    print("按规则归因 TOP：")
    for rid, n in c.most_common(20):
        print(f"  {rid:14s} {n}")
    print("明细写入", args.out)


if __name__ == "__main__":
    main()

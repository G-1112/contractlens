"""合并标注页导出的 labels_filled.json → data/evalset_real/labels.json。

校验项：
  1) 合同集合与当前模板一致（防止拿错文件）；
  2) 每份合同的条款 index 集合一致（防止漏题/串行错位）；
  3) 每条 is_risk 非空、level 合法、风险项有 risk_type。

用法：
  python scripts/merge_annotations.py --file ~/Downloads/labels_filled.json --dry-run
  python scripts/merge_annotations.py --file ~/Downloads/labels_filled.json
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABELS = ROOT / "data" / "evalset_real" / "labels.json"
VALID_LEVELS = {"高", "中", "低", "无"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, type=Path, help="标注页导出的 labels_filled.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.file.exists():
        print(f"[x] 文件不存在：{args.file}")
        return 1
    cur = json.loads(LABELS.read_text(encoding="utf-8"))
    new = json.loads(args.file.read_text(encoding="utf-8"))

    problems: list[str] = []
    if set(cur) != set(new):
        missing = set(cur) - set(new)
        extra = set(new) - set(cur)
        if missing:
            problems.append(f"缺少合同：{sorted(missing)[:5]}")
        if extra:
            problems.append(f"多出合同：{sorted(extra)[:5]}")

    total = filled = 0
    per: dict[str, tuple[int, int]] = {}
    for fn in cur:
        if fn not in new:
            continue
        a = {c["index"] for c in cur[fn]["clauses"]}
        b = {c["index"] for c in new[fn]["clauses"]}
        if a != b:
            problems.append(f"{fn}: 条款索引不一致（缺 {sorted(a-b)[:5]}，多 {sorted(b-a)[:5]}）")
            continue
        n_ok = 0
        for c in new[fn]["clauses"]:
            total += 1
            lv = c.get("level")
            if c.get("is_risk") is None:
                problems.append(f"{fn} 第{c['index']}条: 未标注")
                continue
            if lv not in VALID_LEVELS:
                problems.append(f"{fn} 第{c['index']}条: level 非法 {lv!r}")
                continue
            if c["is_risk"] and not c.get("risk_type"):
                problems.append(f"{fn} 第{c['index']}条: 判为风险但缺 risk_type")
                continue
            if not c["is_risk"] and lv != "无":
                problems.append(f"{fn} 第{c['index']}条: 非风险却标了 {lv}")
                continue
            n_ok += 1
            filled += n_ok - (n_ok - 1)  # 每条 +1
        per[fn] = (n_ok, len(new[fn]["clauses"]))

    print(f"待合并：{len(new)} 份合同 / {total} 条条款，通过校验 {filled} 条")
    for fn in sorted(per):
        ok, n = per[fn]
        print(f"  {fn:22s} {ok:3d}/{n:3d}")

    if problems:
        print(f"\n发现 {len(problems)} 个问题（前 10 条）：")
        for p in problems[:10]:
            print("  -", p)
        print("\n未写入。修好后再跑。")
        return 1

    if args.dry_run:
        print("\n[dry-run] 校验通过，未写入")
        return 0

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(LABELS, LABELS.with_suffix(f".json.bak-{ts}"))
    LABELS.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {LABELS}（备份 labels.json.bak-{ts}）")
    print("下一步：python scripts/annotate_real.py --validate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

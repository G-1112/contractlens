"""条款级准确率评测（大赛 PPT「结果解读」实证数据）。

读 data/evalset 的合成标注集，跑完整流水线，计算：
  - 条款级 精确率 P / 召回率 R / F1（二分类：是否应判为风险）
  - 等级准确率（检出的条目中，等级与标注一致的比例）
  - 分合同类型明细
  - 误报（FP）来源分析：区分规则命中与 LLM/启发式命中

用法：python scripts/evaluate_accuracy.py
输出：控制台 + docs/eval-accuracy.md
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from api.pipeline import parse_file, run_analysis  # noqa: E402

EVALSET = ROOT / "data" / "evalset"
CONTRACTS = EVALSET / "contracts"
LABELS = EVALSET / "labels.json"
REPORT = ROOT / "docs" / "eval-accuracy.md"

# 报告头必须自证两件事：数据集是合成还是真实、运行环境是 mock 还是真实 LLM。
# 此前两者都被写死（无论跑哪个集都写「合成样例集」「mock 模式」），真实集跑出来的
# 指标会被标注成合成口径，对外引用会误判。
IS_SYNTHETIC = True  # 由 main() 依据 --evalset 覆盖


def _env_desc() -> str:
    from api.config import get_settings

    s = get_settings()
    if s.llm_api_key:
        return (
            f"真实 LLM（{s.llm_model}，并发={s.llm_concurrency}）——与线上部署同口径"
        )
    return "mock 模式（无 LLM Key，规则引擎与法规检索全量生效）"


def _safe_div(a: float, b: float) -> float:
    return a / b if b else 0.0


async def run(split: list[str] | None = None, dump: Path | None = None) -> dict:
    labels_all = json.loads(LABELS.read_text(encoding="utf-8"))
    if split is not None:
        missing = [f for f in split if f not in labels_all]
        if missing:
            raise SystemExit(f"[x] split 中的合同不在标注集：{missing[:3]}")
        labels = {f: labels_all[f] for f in split}
    else:
        labels = labels_all

    # 防呆（2026-11-02）：标注未完成的条款 is_risk 为 None，若照常打分，
    # `if gold_risk` 会把 None 当作「非风险」，把真阳性全算成 FP/FN，
    # 输出一组看似合理却完全错误的指标。宁可直接拒绝，也不出假数字。
    pending = [
        f"{fn} 第{c['index']}条"
        for fn, meta in labels.items()
        for c in meta["clauses"]
        if c.get("is_risk") is None
    ]
    if pending:
        raise SystemExit(
            f"[x] 标注未完成：还有 {len(pending)} 条 is_risk 为空（示例：{pending[:3]}）。\n"
            "    先完成标注（data/evalset_real/annotate.html 或 annotate_real.py --interactive），"
            "再跑本脚本。"
        )

    per_type: dict[str, dict[str, int]] = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0, "level_ok": 0})
    fp_by_source: dict[str, int] = defaultdict(int)
    total_c = 0   # 实际参与判定的标注条款数
    tn = 0        # 金标无风险且系统未命中（真负例）
    dump_rows: list[dict] = []

    for filename, meta in labels.items():
        ctype = meta["contract_type"]
        path = CONTRACTS / filename
        parsed = parse_file(path, filename, ctype)
        report = await run_analysis(parsed, ctype, f"eval-{filename}")

        # clause_id → 条款序号
        idx_of = {c.id: i for i, c in enumerate(parsed.clauses)}
        pred: dict[int, tuple[str, str]] = {}
        item_info: dict[int, tuple] = {}
        for it in report.items:
            if it.clause_id.startswith("missing-"):
                continue  # 文档级缺失项，不参与条款级评测
            i = idx_of.get(it.clause_id)
            if i is None:
                continue
            prev = pred.get(i)
            # 同一条款多条命中时，取等级最高的一条作为预测
            order = {"高": 3, "中": 2, "低": 1, "无": 0}
            if prev is None or order.get(it.final_level, 0) > order.get(prev[0], 0):
                pred[i] = (it.final_level, it.source)
                item_info[i] = (it.risk_type, ",".join(it.rule_ids), it.explain[:80])

        for g in meta["clauses"]:
            i = g["index"]
            gold_risk = g["is_risk"]
            got = pred.get(i)
            pred_risk = got is not None and got[0] != "无"
            total_c += 1

            if gold_risk and pred_risk:
                per_type[ctype]["tp"] += 1
                if got and got[0] == g["level"]:
                    per_type[ctype]["level_ok"] += 1
            elif not gold_risk and pred_risk:
                per_type[ctype]["fp"] += 1
                if got:
                    fp_by_source[got[1]] += 1
            elif gold_risk and not pred_risk:
                per_type[ctype]["fn"] += 1
            else:
                tn += 1

            dump_rows.append({
                "contract": filename,
                "type": ctype,
                "index": i,
                "text": g["text"],
                "gold_risk": gold_risk,
                "gold_level": g.get("level"),
                "pred_level": got[0] if got else None,
                "pred_source": got[1] if got else None,
                "risk_type": item_info[i][0] if got else "",
                "rule_ids": item_info[i][1] if got else "",
                "explain": item_info[i][2] if got else "",
            })

    if dump is not None:
        dump.write_text(json.dumps(dump_rows, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"逐条预测明细已写入：{dump}")

    return {
        "per_type": dict(per_type),
        "fp_by_source": dict(fp_by_source),
        "totals": {"annotated": total_c, "tn": tn, "contracts": len(labels)},
    }


def render(result: dict) -> str:
    per_type = result["per_type"]
    anno_method = "未声明"
    try:
        _lb = json.loads(LABELS.read_text(encoding="utf-8"))
        for _e in _lb.values():
            if isinstance(_e.get("annotation"), dict) and _e["annotation"].get("method"):
                anno_method = _e["annotation"]["method"]
                break
    except Exception:  # noqa: BLE001
        pass
    tot = {"tp": 0, "fp": 0, "fn": 0, "level_ok": 0}
    for v in per_type.values():
        for k in tot:
            tot[k] += v[k]

    p = _safe_div(tot["tp"], tot["tp"] + tot["fp"])
    r = _safe_div(tot["tp"], tot["tp"] + tot["fn"])
    f1 = _safe_div(2 * p * r, p + r) if (p + r) else 0.0
    acc = _safe_div(tot["level_ok"], tot["tp"])

    lines = [
        f"# 条款级准确率实测（{'合成样例集' if IS_SYNTHETIC else '真实合同标注集'}）",
        "",
        (
            "> ⚠️ **数据性质**：测试集为 `scripts/build_evalset.py` 程序合成的样例，"
            "由规则库关键词套用句式模板生成，标注由构造过程确定。"
            "**不代表真实合同分布**，用于规则引擎回归度量，引用时须标注「合成样例」。"
            if IS_SYNTHETIC
            else "> **数据性质**：真实合同 + 逐条标注（来源登记见 data/evalset_real/provenance.json）。"
            f"标注方式：{anno_method}。"
            "样本量小、来源分布单一，置信区间宽，引用时须同时给出样本量与口径。"
        ),
        f"> 样本：{result['totals']['contracts']} 份合同，",
        f"标注条款 {result['totals']['annotated']} 条（风险 {tot['tp'] + tot['fn']} 条），"
        f"其中 TP+FP+FN {tot['tp'] + tot['fp'] + tot['fn']} 条、真负例 {result['totals']['tn']} 条。",
        f"> 运行环境：本机 {_env_desc()}。",
        "",
        "## 总体指标",
        "",
        "| 指标 | 数值 | 计算式 |",
        "|---|---|---|",
        f"| 精确率 P | **{p:.4f}** | TP/(TP+FP) = {tot['tp']}/({tot['tp']}+{tot['fp']}) |",
        f"| 召回率 R | **{r:.4f}** | TP/(TP+FN) = {tot['tp']}/({tot['tp']}+{tot['fn']}) |",
        f"| F1 | **{f1:.4f}** | 2PR/(P+R) |",
        f"| 等级准确率 | **{acc:.4f}** | 等级一致数/TP = {tot['level_ok']}/{tot['tp']} |",
        f"| TP / FP / FN | {tot['tp']} / {tot['fp']} / {tot['fn']} | — |",
        "",
        "## 分合同类型明细",
        "",
        "| 合同类型 | TP | FP | FN | P | R | F1 |",
        "|---|---|---|---|---|---|---|",
    ]
    for ctype, v in sorted(per_type.items()):
        tp, fp, fn = v["tp"], v["fp"], v["fn"]
        pp = _safe_div(tp, tp + fp)
        rr = _safe_div(tp, tp + fn)
        ff = _safe_div(2 * pp * rr, pp + rr) if (pp + rr) else 0.0
        lines.append(f"| {ctype} | {tp} | {fp} | {fn} | {pp:.4f} | {rr:.4f} | {ff:.4f} |")

    lines += ["", "## 误报（FP）来源分析", "", "| 判定来源 | FP 数 |", "|---|---|"]
    for src, n in sorted(result["fp_by_source"].items(), key=lambda x: -x[1]):
        lines.append(f"| {src} | {n} |")
    from api.config import get_settings as _gs
    if _gs().llm_api_key:
        lines += [
            "",
            "说明：`llm_only` 误报来自 LLM 直接判定（未获规则或检索佐证即标记风险），",
            "`rule_override` 来自规则抬级与 LLM 判定不一致。两者是收敛迭代的主要对象。",
            "",
        ]
    else:
        lines += [
            "",
            "说明：`llm_only` 的误报主要来自 mock 模式的确定性启发式（无 LLM Key 时的降级策略），",
            "接入真实 LLM 后该部分会被语义判定替换，指标需重新实测。",
            "",
        ]
    lines += [
        "## 复现命令",
        "",
        "```bash",
        "python scripts/build_evalset.py      # 生成合成标注集",
        "python scripts/evaluate_accuracy.py   # 跑评测，输出到本文件",
        "```",
    ]
    return "\n".join(lines) + "\n"


async def main() -> None:
    global CONTRACTS, LABELS, REPORT, IS_SYNTHETIC
    import argparse

    ap = argparse.ArgumentParser(description="条款级准确率评测")
    ap.add_argument(
        "--evalset",
        type=Path,
        default=EVALSET,
        help="评测集目录（默认 data/evalset 合成集；真实集传 --evalset data/evalset_real）",
    )
    ap.add_argument("--report", type=Path, default=None, help="报告输出路径")
    ap.add_argument(
        "--split",
        choices=["dev", "holdout", "all"],
        default="all",
        help="合同分组（读 data/evalset_real/split.json；all=不过滤）",
    )
    ap.add_argument("--dump", type=Path, default=None, help="逐条预测明细 JSON 输出路径")
    args = ap.parse_args()

    evalset = args.evalset if args.evalset.is_absolute() else ROOT / args.evalset
    CONTRACTS = evalset / "contracts"
    LABELS = evalset / "labels.json"
    IS_SYNTHETIC = evalset.name == "evalset"
    if args.report:
        REPORT = args.report
    elif not IS_SYNTHETIC and args.split != "all":
        REPORT = ROOT / "docs" / "iteration" / "regional_v2" / f"eval-real-{args.split}.md"
    elif not IS_SYNTHETIC:
        REPORT = ROOT / "docs" / "eval-accuracy-real.md"

    if not LABELS.exists():
        print(f"[x] 找不到标注文件：{LABELS}")
        print("    真实集需先运行：python scripts/annotate_real.py --init && --interactive <文件>")
        return

    split: list[str] | None = None
    if args.split != "all":
        split_file = evalset / "split.json"
        if not split_file.exists():
            raise SystemExit(f"[x] 找不到分组文件：{split_file}")
        groups = json.loads(split_file.read_text(encoding="utf-8"))
        split = groups[args.split]
        print(f"分组={args.split}：{len(split)} 份合同 {split}")

    result = await run(split=split, dump=args.dump)
    text = render(result)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(text, encoding="utf-8")
    print(text)
    print(f"报告已写入：{REPORT}")


if __name__ == "__main__":
    asyncio.run(main())

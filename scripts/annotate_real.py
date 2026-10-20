"""真实合同人工标注工具（P1-5）。

为什么需要：现有评测集 50 份全部由 scripts/build_evalset.py **程序合成**
（规则关键词套句式模板），F1 0.9597 反映的是规则覆盖与自洽性，不代表真实
业务分布；且 21 个 FP 全部来自 llm_only（mock 启发式）。对外材料若只给这组
数字，会被质疑「自证清白」。本工具用于把**真实合同**接入评测链。

关键约束（防止把合成样本伪装成真实样本）：
  1. 真实集独立存放在 data/evalset_real/，与合成集物理隔离；
  2. 每份合同必须在 provenance.json 登记来源、取得方式与授权，否则 --validate
     直接判为不合规；
  3. 标注必须人工确认，is_risk 不允许留空通过校验。

用法：
  # 1) 把真实合同（docx/txt）放进 data/evalset_real/contracts/
  # 2) 生成标注模板（自动预填条款文本，标注项留空）
  python scripts/annotate_real.py --init
  # 3) 交互标注（逐条 y/n + 等级 + 风险类型）
  python scripts/annotate_real.py --interactive rent_01.docx
  # 4) 校验完整性与来源登记
  python scripts/annotate_real.py --validate
  # 5) 用真实集跑评测（与合成集分开出报告）
  python scripts/evaluate_accuracy.py --evalset data/evalset_real
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.parser import split_clauses  # noqa: E402
from api.schemas import DocumentLine  # noqa: E402

REAL_DIR = ROOT / "data" / "evalset_real"
CONTRACTS = REAL_DIR / "contracts"
LABELS = REAL_DIR / "labels.json"
PROVENANCE = REAL_DIR / "provenance.json"

VALID_LEVELS = {"高", "中", "低", "无"}
VALID_TYPES = {
    "利率", "违约金", "担保", "隐私", "个人信息", "还款", "缺失",
    "解除", "争议", "其他", "费用", "期限", "格式条款", "免除",
}

_CTYPE_BY_NAME = {
    "rent": "rent", "租赁": "rent", "劳动": "labor", "实习": "intern",
    "intern": "intern", "loan": "loan", "借款": "loan", "借贷": "loan",
    "guarantee": "guarantee", "保证": "guarantee", "担保": "guarantee",
}


def guess_ctype(filename: str) -> str:
    for k, v in _CTYPE_BY_NAME.items():
        if k in filename.lower() or k in filename:
            return v
    return "rent"


def clause_texts(path: Path) -> list[str]:
    """复用pipeline.parse_file 的解析链路，保证标注对象与审查对象完全一致。

    注意：DocxParser/PdfParser.parse() 返回的是 (lines, has_text) 元组，
    不是 ParsedDocument；条款切分在 pipeline.parse_file 里统一完成，
    这里直接用它，避免自己再实现一遍切分逻辑导致口径漂移。
    """
    suffix = path.suffix.lower()
    if suffix in {".docx", ".doc", ".pdf"}:
        from api.pipeline import parse_file

        parsed = parse_file(path, path.name, guess_ctype(path.name))
        return [c.text for c in parsed.clauses]
    # 纯文本：走与 pipeline 相同的 DocumentLine + split_clauses
    raw = path.read_text(encoding="utf-8", errors="ignore")
    lines = [DocumentLine(text=ln.strip(), page=1) for ln in raw.splitlines() if ln.strip()]
    return [c.text for c in split_clauses(lines)]


def cmd_init() -> int:
    if not CONTRACTS.is_dir():
        print(f"[!] 目录不存在：{CONTRACTS}\n    请先放入真实合同（.docx/.txt）")
        return 1
    files = sorted(
        p for p in CONTRACTS.iterdir() if p.suffix.lower() in {".docx", ".doc", ".txt"}
    )
    if not files:
        print(f"[!] {CONTRACTS} 下没有 .docx/.txt 文件")
        return 1

    labels: dict[str, dict] = {}
    if LABELS.exists():
        labels = json.loads(LABELS.read_text(encoding="utf-8"))
        print(f"[i] 已有 {len(labels)} 份标注，保留并补充新文件")

    added = 0
    for f in files:
        if f.name in labels:
            continue
        try:
            texts = clause_texts(f)
        except Exception as e:  # noqa: BLE001
            print(f"[x] {f.name} 解析失败：{type(e).__name__}: {e}")
            continue
        labels[f.name] = {
            "contract_type": guess_ctype(f.name),
            "clause_count": len(texts),
            "risk_clauses": None,  # 待人工填写
            "clauses": [
                {
                    "index": i,
                    "text": t,
                    "is_risk": None,  # 待人工标注
                    "level": None,
                    "risk_type": "",
                    "rule_id": "",
                    "annotator_note": "",
                }
                for i, t in enumerate(texts)
            ],
        }
        added += 1
        print(f"[+] {f.name}: {len(texts)} 条条款（{labels[f.name]['contract_type']}）")

    LABELS.parent.mkdir(parents=True, exist_ok=True)
    LABELS.write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n模板已写入 {LABELS}（新增 {added} 份，共 {len(labels)} 份）")
    if not PROVENANCE.exists():
        PROVENANCE.write_text(
            json.dumps(
                {
                    "_说明": "每份合同登记来源；未登记来源的文件会被 --validate 判为不合规",
                    "_字段": ["source（来源/公开链接）", "obtained（取得方式与日期）", "license（授权/可公开性）"],
                    "contracts": {},
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"来源登记表已创建 {PROVENANCE}，请逐份补全后再运行 --validate")
    return 0


def cmd_interactive(filename: str) -> int:
    if not LABELS.exists():
        print("[!] 请先运行 --init 生成模板")
        return 1
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    if filename not in labels:
        print(f"[!] {filename} 不在标注模板中，先跑 --init")
        return 1
    entry = labels[filename]
    print(f"\n=== {filename}（{entry['contract_type']}，{entry['clause_count']} 条）===")
    print("等级：h=高 m=中 l=低 n=无（直接回车=跳过，沿用原值）\n")
    for c in entry["clauses"]:
        print(f"[{c['index']}] {c['text']}")
        cur = c["level"] or "-"
        ans = input(f"    风险? (y/n，当前 {c['is_risk']}) ").strip().lower()
        if ans:
            c["is_risk"] = ans.startswith("y")
        if c["is_risk"]:
            lv = input(f"    等级 (h/m/l/n，当前 {cur}) ").strip().lower()
            c["level"] = {"h": "高", "m": "中", "l": "低", "n": "无"}.get(lv, c["level"] or "中")
            rt = input(f"    风险类型 [{c['risk_type'] or '其他'}] ").strip()
            c["risk_type"] = rt or c["risk_type"] or "其他"
        else:
            c["level"] = "无"
            c["risk_type"] = ""
        note = input("    备注(可空) ").strip()
        if note:
            c["annotator_note"] = note
        print()
    entry["risk_clauses"] = sum(1 for c in entry["clauses"] if c.get("is_risk"))
    LABELS.write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已保存：{LABELS}")
    return 0


def cmd_validate() -> int:
    problems: list[str] = []
    if not LABELS.exists():
        print("[x] 没有 labels.json，先跑 --init")
        return 1
    labels = json.loads(LABELS.read_text(encoding="utf-8"))

    prov = {"contracts": {}}
    if PROVENANCE.exists():
        prov = json.loads(PROVENANCE.read_text(encoding="utf-8"))
    registered = set((prov.get("contracts") or {}).keys())

    total_c = done_c = 0
    for name, e in sorted(labels.items()):
        if name not in registered:
            problems.append(f"{name}:未在 provenance.json 登记来源（防伪造真实样本）")
        for c in e["clauses"]:
            total_c += 1
            if c.get("is_risk") is None:
                problems.append(f"{name} 第{c['index']}条:is_risk 未标注")
                continue
            done_c += 1
            if c.get("level") not in VALID_LEVELS:
                problems.append(f"{name} 第{c['index']}条:等级非法 {c.get('level')!r}")
            if c.get("is_risk") and not c.get("risk_type"):
                problems.append(f"{name} 第{c['index']}条:判为风险但缺 risk_type")
            if not c.get("is_risk") and c.get("level") not in (None, "无"):
                problems.append(f"{name} 第{c['index']}条:非风险却标了等级 {c.get('level')!r}")

    print(f"合同 {len(labels)} 份 / 条款 {total_c} 条，已标注 {done_c} 条"
          f"（{done_c/total_c:.1%}）" if total_c else "无数据")
    print(f"来源登记 {len(registered)} 份")
    if problems:
        print(f"\n发现 {len(problems)} 个问题：")
        for p in problems[:60]:
            print("  -", p)
        if len(problems) > 60:
            print(f"  … 另有 {len(problems)-60} 条")
        return 1
    print("\n✅ 校验通过：来源齐备、标注完整、等级与风险类型自洽")
    print("下一步：python scripts/evaluate_accuracy.py --evalset data/evalset_real")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--init", action="store_true", help="生成/补充标注模板")
    g.add_argument("--interactive", metavar="FILE", help="交互标注某份合同")
    g.add_argument("--validate", action="store_true", help="校验完整性与来源登记")
    args = ap.parse_args()

    if args.init:
        return cmd_init()
    if args.interactive:
        return cmd_interactive(args.interactive)
    return cmd_validate()


if __name__ == "__main__":
    raise SystemExit(main())
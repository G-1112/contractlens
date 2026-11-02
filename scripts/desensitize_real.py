"""真实合同脱敏（data/evalset_real/contracts/*.docx）。

原则：
  - 只替换**可识别的身份/联系信息**，不动合同条款本身（避免改变语义）；
  - 每类替换计数并落报告，替换明细写进 docs/真实合同脱敏报告.md；
  - 姓名不自动替换（易误伤「甲方/乙方/保证人」等角色词），改为列出候选供人工复核。

用法：
  python scripts/desensitize_real.py            # 执行并写报告
  python scripts/desensitize_real.py --check    # 只扫描不修改
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

from docx import Document

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "data" / "evalset_real" / "contracts"
REPORT = ROOT / "docs" / "真实合同脱敏报告.md"

# 已知的真实主体名（来自公开转载的签署版），按长名优先替换
KNOWN_ENTITIES = [
    "乐清市虹桥职业技术学校",
    "泰安市科技中等专业学校",
    "东平县职业中等专业学校",
]

RULES: list[tuple[str, re.Pattern[str], str]] = [
    ("统一社会信用代码", re.compile(r"(?<![0-9A-Z])[0-9A-HJ-NPQRTUWXY]{18}(?![0-9A-Z])"), "【统一社会信用代码】"),
    ("身份证号", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "【身份证号】"),
    ("身份证号（15位）", re.compile(r"(?<!\d)\d{15}(?!\d)"), "【身份证号】"),
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "【手机号】"),
    ("括号电话", re.compile(r"\(\d{10,11}\)?"), "【固定电话】"),
    ("固定电话", re.compile(r"(?<!\d)0\d{2,3}[-－]?\d{7,8}(?!\d)"), "【固定电话】"),
    ("银行账号", re.compile(r"(?<!\d)\d{16,19}(?!\d)"), "【银行账号】"),
    ("电子邮箱", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "【电子邮箱】"),
]

# 术语表（用户自备材料中的特定单位/人名/地址）
TERMS_FILE = ROOT / "data" / "evalset_real" / "pii_terms.json"


def load_terms() -> dict:
    """返回完整术语表（含 replacement 映射字典）。

    注意：不要用 isinstance(v, list) 过滤——replacement 是 dict，
    被滤掉会让占位符退化成 【org】/【person】 这种英文标记。
    """
    if not TERMS_FILE.exists():
        return {}
    return json.loads(TERMS_FILE.read_text(encoding="utf-8"))


def term_pattern(term: str) -> re.Pattern[str]:
    """允许字符间夹空格（OCR/Word 常把词拆开）。"""
    return re.compile(r"\s*".join(map(re.escape, term)))


def scan_text(text: str) -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    for name, pat, _ in RULES:
        for m in pat.finditer(text):
            hits.append((name, m.group(0)))
    for ent in KNOWN_ENTITIES:
        if re.search(r"\s*".join(map(re.escape, ent)), text):
            hits.append(("已知主体名", ent))
    for cat, items in load_terms().items():
        if cat == "replacement" or not isinstance(items, list):
            continue
        for term in items:
            if re.search(term_pattern(term), text):
                hits.append((f"术语-{cat}", term))
    return hits


def desensitize_text(text: str) -> tuple[str, list[tuple[str, str]]]:
    hits: list[tuple[str, str]] = []
    out = text

    # 1) 术语表（单位/人名/地址）——按类别替换为占位符
    terms = load_terms()
    repl_map = terms.get("replacement", {})
    for cat, items in terms.items():
        if cat == "replacement" or not isinstance(items, list):
            continue
        marker = repl_map.get(cat, f"【{cat}】")
        for term in sorted(items, key=len, reverse=True):  # 长词优先，避免只替一半
            pat = term_pattern(term)
            for m in pat.finditer(out):
                hits.append((f"术语-{cat}", m.group(0)))
            out = pat.sub(marker, out)

    # 2) 已知主体名兜底
    for ent in KNOWN_ENTITIES:
        pat = re.compile(r"\s*".join(map(re.escape, ent)))
        for m in pat.finditer(out):
            hits.append(("已知主体名", m.group(0)))
        out = pat.sub("【学校名称】", out)

    # 3) 正则类（证件号/电话/账号/邮箱）
    for name, pat, repl in RULES:
        found = pat.findall(out)
        for f in found:
            hits.append((name, f if isinstance(f, str) else str(f)))
        out = pat.sub(repl, out)
    return out, hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只扫描不修改")
    args = ap.parse_args()

    files = sorted(CONTRACTS.glob("*.docx"))
    rows: list[tuple[str, dict[str, int], list[str]]] = []
    total_by_kind: dict[str, int] = {}

    for f in files:
        doc = Document(str(f))
        counts: dict[str, int] = {}
        samples: list[str] = []
        changed = False

        for para in doc.paragraphs:
            text = para.text
            if not text.strip():
                continue
            hits = scan_text(text)
            if not hits:
                continue
            if args.check:
                counts_local: dict[str, int] = {}
                for kind, val in hits:
                    counts_local[kind] = counts_local.get(kind, 0) + 1
                for k, v in counts_local.items():
                    counts[k] = counts.get(k, 0) + v
                samples.extend(f"{kind}:{val[:20]}" for kind, val in hits[:3])
                continue

            new_text, used = desensitize_text(text)
            if new_text != text:
                changed = True
                for kind, val in used:
                    counts[kind] = counts.get(kind, 0) + 1
                    if len(samples) < 5:
                        samples.append(f"{kind}:{val[:18]}")
                # 只在单 run 段落下替换，避免破坏复杂格式
                if len(para.runs) == 1:
                    para.runs[0].text = new_text
                else:
                    # 多 run：整段重写为单 run（合同文本无复杂格式，可接受）
                    for r in para.runs[1:]:
                        r.text = ""
                    if para.runs:
                        para.runs[0].text = new_text

        if changed and not args.check:
            doc.save(str(f))
        if counts:
            rows.append((f.name, counts, samples))
            for k, v in counts.items():
                total_by_kind[k] = total_by_kind.get(k, 0) + v

    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    lines = [
        "# 真实合同脱敏报告",
        "",
        f"> 生成时间：{now}；脚本：`scripts/desensitize_real.py`（可复跑）。",
        "> 口径：仅替换可识别身份/联系信息，不改动条款语义；姓名不自动替换，见文末人工复核清单。",
        "",
        f"## 总览（{'仅扫描' if args.check else '已执行替换'}）",
        "",
        "| 类别 | 命中数 |",
        "|---|---|",
    ]
    for k, v in sorted(total_by_kind.items(), key=lambda x: -x[1]):
        lines.append(f"| {k} | {v} |")
    if not total_by_kind:
        lines.append("| （无命中） | 0 |")

    lines += ["", "## 逐文件明细", "", "| 文件 | 命中 | 样例（截断） |", "|---|---|---|"]
    for name, counts, samples in rows:
        c = "；".join(f"{k}×{v}" for k, v in counts.items())
        lines.append(f"| {name} | {c} | {'；'.join(samples)[:80] or '—'} |")
    if not rows:
        lines.append("| — | — | — |")

    lines += [
        "",
        "## 结果与限制",
        "",
        "- 空白示范文本本身不含个人信息，命中主要来自**公开转载的签署版**（校名、联系电话等）。",
        "- 身份证/手机号/银行账号/邮箱按正则替换为固定占位符，替换后不影响条款结构。",
        "- **姓名未自动替换**：合同中的「甲方/乙方/保证人」等角色词与真实人名难以用正则区分，"
        "误替换会破坏条款语义。请人工通读 20 份文本确认无残留姓名（重点：签署页、联系人栏）。",
        "",
        "## 人工复核清单",
        "",
        "| 文件 | 需人工确认项 |",
        "|---|---|",
    ]
    for f in files:
        lines.append(f"| {f.name} | 签署页/联系人栏是否残留真实姓名 |")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    if not args.check:
        REPORT.write_text("\n".join(lines), encoding="utf-8")

    print(f"{'扫描' if args.check else '脱敏'}完成：{len(files)} 份文件，命中类型 {len(total_by_kind)} 类")
    for k, v in sorted(total_by_kind.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")
    if not args.check:
        print(f"报告：{REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

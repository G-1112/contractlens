"""匿名评审脱敏扫描（P3-8）。

规则依据：
- 《报名须知》五.2：用于匿名评审的材料，不得出现学校、单位、团队成员姓名、
  Logo、水印或其他身份识别信息。
- 《作品提交说明》首页：提交作品内不得出现学校、学生姓名和指导教师信息，
  否则取消参赛资格。

⚠️ 例外（不算违规）：PPT / 视频的**文件名**必须写成
  【命题领域-学校-队长姓名-作品名称】（作品提交说明二、三），
  文件名含身份信息是规则要求，与「内容脱敏」不冲突。本脚本只扫内容，不扫文件名。

用法：
  python scripts/scan_anonymity.py                 # 扫默认范围（仓库 + 交付物）
  python scripts/scan_anonymity.py --ppt <path>   # 追加指定 pptx/docx 交付物
  python scripts/scan_anonymity.py --write docs/脱敏扫描报告.md

退出码：0 = 无命中；1 = 有命中（阻断提交）。
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent

# ── 身份识别模式 ────────────────────────────────────────────────────────────
# 每条：(模式名, 正则, 说明, 是否允许出现在某些语境)
IDENTITY_PATTERNS: list[tuple[str, str, str]] = [
    ("学校名", r"太原科技大学", "学校名称"),
    ("学校名简称", r"太科大|太原科大", "学校简称"),
    ("学院/承办单位", r"电子信息工程学院", "校赛承办二级单位"),
    ("队长姓名", r"葛铭坤", "队长/队员姓名"),
    ("路径姓名", r"李凯", "本地路径中的姓名（非提交物，仅提示）"),
    ("学号", r"202320040410", "学号"),
    ("导师姓名", r"翁自觉", "指导教师姓名"),
    ("邮箱", r"[\w.+-]+@qq\.com|1539654408|13956456913", "联系邮箱/账号"),
    ("手机号", r"(?<!\d)1[3-9]\d{9}(?!\d)", "手机号"),
    ("GitHub账号", r"\bG-1112\b", "GitHub 账号名"),
    ("区域赛承办方", r"山西电子科技学院", "同区域其他承办单位"),
]

# 允许命中的语境：这些文件本就记录身份，不属于「评审材料」
ALLOW_CONTEXT = ("scan_anonymity",)

TEXT_SUFFIX = {".md", ".txt", ".ts", ".tsx", ".js", ".jsx", ".json", ".yaml", ".yml", ".py", ".html", ".css"}
SKIP_DIR = {".git", "node_modules", "dist", "site", ".venv", "__pycache__", ".pytest_cache", "screenshots", "raw"}

# 交付物：不在仓库内，需要显式扫描
DEFAULT_DELIVERABLES = [
    WORKSPACE / "全国AI应用创新大赛-金融方向-合同智能审查Agent.pptx",
    WORKSPACE / "通用赛道方案-金融方向-合同智能审查Agent.html",
]


def _zip_texts(path: Path) -> list[tuple[str, str]]:
    """从 pptx/docx 提取所有 XML 文本片段。"""
    out: list[tuple[str, str]] = []
    try:
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if not name.endswith(".xml"):
                    continue
                if not (name.startswith("ppt/") or name.startswith("word/")):
                    continue
                try:
                    raw = z.read(name).decode("utf-8", errors="ignore")
                except Exception:  # noqa: BLE001
                    continue
                for t in re.findall(r"<a:t>([^<]*)</a:t>", raw):
                    out.append((name, t))
                # docx 用 <w:t>
                for t in re.findall(r"<w:t[^>]*>([^<]*)</w:t>", raw):
                    out.append((name, t))
    except zipfile.BadZipFile:
        pass
    return out


def iter_targets(extra: list[Path]) -> list[tuple[Path, str]]:
    """返回 [(路径, 待扫文本的宿主描述)]。文件类直接给路径，zip 类标记为 zip。"""
    targets: list[tuple[Path, str]] = []
    for dirpath, dirnames, filenames in __import__("os").walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR]
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.suffix.lower() in TEXT_SUFFIX:
                targets.append((p, "file"))
            elif p.suffix.lower() in {".docx", ".pptx"}:
                targets.append((p, "zip"))
    for p in extra:
        if p.exists():
            targets.append((p, "zip" if p.suffix.lower() in {".docx", ".pptx"} else "file"))
    return targets


def scan(targets: list[tuple[Path, str]]) -> list[tuple[str, str, str, str]]:
    """返回 [(类别, 文件, 位置, 命中片段)]。"""
    hits: list[tuple[str, str, str, str]] = []
    for path, kind in targets:
        rel = path.relative_to(WORKSPACE) if str(path).startswith(str(WORKSPACE)) else path
        if any(a in str(rel) for a in ALLOW_CONTEXT):
            continue
        if kind == "zip":
            for member, text in _zip_texts(path):
                if not text.strip():
                    continue
                for name, pat, _desc in IDENTITY_PATTERNS:
                    if re.search(pat, text):
                        hits.append((name, str(rel), member, text.strip()[:80]))
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            continue
        for lineno, line in enumerate(content.splitlines(), 1):
            for name, pat, _desc in IDENTITY_PATTERNS:
                if re.search(pat, line):
                    hits.append((name, str(rel), f"L{lineno}", line.strip()[:100]))
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ppt", nargs="*", default=[], help="额外扫描的 pptx/docx/html 交付物")
    ap.add_argument("--write", type=Path, default=None, help="把 markdown 报告写到该路径")
    args = ap.parse_args()

    extra = [Path(x) for x in args.ppt] + DEFAULT_DELIVERABLES
    targets = iter_targets(extra)
    hits = scan(targets)

    lines = [
        "# 匿名评审脱敏扫描报告",
        "",
        "> 规则依据：《报名须知》五.2、《作品提交说明》首页——提交材料不得出现",
        "> 学校、单位、团队成员姓名、Logo、水印或其他身份识别信息。",
        "> 例外：PPT/视频**文件名**须为【命题领域-学校-队长姓名-作品名称】，",
        "> 文件名含身份信息是规则要求，本脚本只扫内容不扫文件名。",
        "",
        f"- 扫描目标：{len(targets)} 个文件/压缩包",
        f"- 命中：**{len(hits)}** 处",
        "",
    ]

    if not hits:
        lines.append("## 结论：无命中 ✅")
        lines.append("")
        lines.append("全部扫描目标未检出学校名、姓名、学号、导师、邮箱、手机号等身份识别信息。")
    else:
        by_cat: dict[str, list[tuple[str, str, str, str]]] = {}
        for cat, f, loc, snip in hits:
            by_cat.setdefault(cat, []).append((cat, f, loc, snip))
        lines.append("## 命中明细")
        lines.append("")
        lines.append("| 类别 | 文件 | 位置 | 片段 |")
        lines.append("|---|---|---|---|")
        for cat, rows in sorted(by_cat.items()):
            for _c, f, loc, snip in rows:
                lines.append(f"| {cat} | `{f}` | {loc} | {snip.replace('|', '/')} |")
        lines += [
            "",
            "## 处置建议",
            "",
            "1. **提交物内**（PPT 内容、前端界面、示例数据、视频画面）必须清零——零容忍，"
            "违反即取消参赛资格。",
            "2. **仓库内**的 README/docs 若要随作品公开，同样需清理；"
            "但本地开发文档（含本脚本自身）可保留，建议在公开前单独跑一次 `--ppt` 指定公开物。",
            "3. 注意 `.env` 里的 LLM Key 绝不入库（.gitignore 已排除），本次扫描不含 Key 校验，"
            "如需校验请另行执行 `git check-ignore -v .env`。",
        ]

    report = "\n".join(lines)
    print(report)
    if args.write:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(report, encoding="utf-8")
        print(f"\n报告已写入：{args.write}")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
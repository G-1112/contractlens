"""合同图片 OCR（实拍/扫描件 → 文本 → docx）。

背景：用户提供 17 张房屋租赁合同实拍图（合同材料/），需接入真实评测集。
管线：RapidOCR（离线中文）→ 按行框坐标重排 → 合并段落 → 清洗 → docx。

用法：
  python scripts/ocr_contract_images.py --dir "../合同材料" --name real_rent_07
  python scripts/ocr_contract_images.py --dir ../合同材料 --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "evalset_real" / "contracts"
PROV = ROOT / "data" / "evalset_real" / "provenance.json"
RAW = ROOT / "_raw" / "ocr"

IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

# OCR 噪声与元信息（与采集脚本同口径，避免把页眉页脚当条款）
_NOISE = [
    r"^\s*[-—_]{2,}\s*\d+\s*[-—_]{2,}\s*$",
    r"^\s*第\s*\d+\s*页\s*(共\s*\d+\s*页)?\s*$",
]


def list_images(d: Path) -> list[Path]:
    return sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in IMG_EXT)


def ocr_image(engine, path: Path) -> list[tuple[float, float, str, float]]:
    """返回 (y, x, text, score)，按阅读顺序（先行后列）排列。"""
    res, _ = engine(str(path))
    rows = []
    for item in res or []:
        box, text, score = item[0], item[1], item[2]
        try:
            sc = float(score)
        except (TypeError, ValueError):
            sc = 0.0
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        rows.append((sum(ys) / len(ys), min(xs), text.strip(), sc))
    # 行内左右拼接：同一行（y 接近）按 x 排序
    rows.sort(key=lambda r: (r[0], r[1]))
    merged: list[tuple[float, float, str, float]] = []
    for y, x, text, sc in rows:
        if merged and abs(y - merged[-1][0]) < 14:
            py, px, ptxt, psc = merged[-1]
            merged[-1] = (py, px, ptxt + text, min(psc, sc))
        else:
            merged.append((y, x, text, sc))
    return merged


def clean_line(s: str) -> str:
    s = s.replace("\u3000", " ").strip()
    for pat in _NOISE:
        if re.match(pat, s):
            return ""
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="图片目录")
    ap.add_argument("--name", default="real_rent_07", help="输出文件名（不含扩展名）")
    ap.add_argument("--title", default="", help="合同名称，写入 provenance（如「房屋租赁合同（实拍）」）")
    ap.add_argument("--ctype", default="rent")
    ap.add_argument("--min-score", type=float, default=0.5, help="低于该置信度的行剔除")
    ap.add_argument("--pages", default="", help="页码范围，如 1-4 或 5-6（留空=全部）")
    ap.add_argument("--from-cache", action="store_true",
                    help="复用 _raw/ocr 下已识别文本，不重新 OCR（改分段时用）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src = (ROOT / args.dir).resolve() if not Path(args.dir).is_absolute() else Path(args.dir)
    imgs = list_images(src)
    if not imgs:
        print(f"[x] {src} 下没有图片")
        return 1

    # 页码区间（一份合同可能只占连续几页：一叠照片里常有 3-5 份合同）
    if args.pages:
        a, _, b = args.pages.partition("-")
        lo, hi = int(a), int(b or a)
        imgs = imgs[lo - 1:hi]
        if not imgs:
            print(f"[x] 页码范围 {args.pages} 无对应图片")
            return 1
        print(f"页码 {args.pages}：{len(imgs)} 张（{imgs[0].name} … {imgs[-1].name}）")
    else:
        print(f"图片 {len(imgs)} 张：{imgs[0].name} … {imgs[-1].name}")

    from rapidocr_onnxruntime import RapidOCR

    engine = None if args.from_cache else RapidOCR()
    RAW.mkdir(parents=True, exist_ok=True)

    all_lines: list[str] = []
    per_page: list[dict] = []
    for i, p in enumerate(imgs, 1):
        if args.from_cache:
            cached = RAW / f"{p.stem}.txt"
            if not cached.exists():
                print(f"[x] 缺少缓存 {cached}，先不带 --from-cache 跑一次")
                return 1
            lines = [ln for ln in cached.read_text(encoding="utf-8").splitlines() if ln.strip()]
        else:
            rows = ocr_image(engine, p)
            lines = [clean_line(t) for _, _, t, sc in rows if sc >= args.min_score]
            lines = [ln for ln in lines if ln]
            (RAW / f"{p.stem}.txt").write_text("\n".join(lines), encoding="utf-8")
        per_page.append({"page": i, "file": p.name, "lines": len(lines),
                         "chars": sum(len(x) for x in lines)})
        all_lines.extend(lines)
        print(f"  [{i:2d}/{len(imgs)}] {p.name}  行 {len(lines):3d}  字 {sum(len(x) for x in lines):4d}")

    text = "\n".join(all_lines)
    print(f"\nOCR 合计 {len(all_lines)} 行 / {len(text)} 字")

    if args.dry_run:
        print("\n--- 前 400 字 ---")
        print(text[:400])
        return 0

    # 写 docx（不注入标题，避免被切成第 0 条）
    from docx import Document

    doc = Document()
    for ln in all_lines:
        doc.add_paragraph(ln)
    out = OUT_DIR / f"{args.name}.docx"
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out)

    prov = json.loads(PROV.read_text(encoding="utf-8"))
    prov["contracts"][out.name] = {
        "source": f"用户提供实拍图片（{src.name}/，{len(imgs)} 张，页码 {args.pages or '全部'}）",
        "obtained": f"RapidOCR 离线识别，{date.today().isoformat()}",
        "license": "用户自备材料，授权用于本项目评测；已脱敏处理",
        "type": "真实签署件（实拍 OCR）",
        "contract_type": args.ctype,
        "name": args.title or f"实拍合同 {args.name}",
        "issuer": "用户提供",
        "ocr": {"pages": len(imgs), "page_range": args.pages or "all",
                "lines": len(all_lines), "chars": len(text),
                "engine": "rapidocr_onnxruntime", "min_score": args.min_score},
    }
    PROV.write_text(json.dumps(prov, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {out}（{len(all_lines)} 段）")
    print(f"已登记来源：{out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

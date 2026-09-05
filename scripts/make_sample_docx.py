"""把 data/samples/*.txt 样本转换为 .docx，便于在前端上传演示。

用法：python scripts/make_sample_docx.py
依赖：python-docx（pip install python-docx）
"""

from __future__ import annotations

from pathlib import Path

try:
    from docx import Document
except ImportError:
    raise SystemExit("请先安装 python-docx：pip install python-docx")


def txt_to_docx(txt_path: Path, docx_path: Path) -> None:
    doc = Document()
    for line in txt_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        # 标题（不含"第X条"的首行）加粗
        if not line.startswith("第") and "条" not in line[:4]:
            doc.add_heading(line, level=1)
        else:
            doc.add_paragraph(line)
    doc.save(str(docx_path))
    print(f"生成 {docx_path}")


def main() -> None:
    samples = Path(__file__).resolve().parent.parent / "data" / "samples"
    for txt in sorted(samples.glob("*.txt")):
        txt_to_docx(txt, txt.with_suffix(".docx"))


if __name__ == "__main__":
    main()

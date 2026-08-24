"""DOCX 解析（FR-02）。

按段落提取文本，保留段落序号。DOCX 无固定分页，统一 page=1，
bbox 用段落顺序近似，仅供前端展示顺序用。
"""

from __future__ import annotations

from pathlib import Path

from ..schemas import DocumentLine


class DocxParser:
    """DOCX → list[DocumentLine]。"""

    def parse(self, path: str | Path) -> tuple[list[DocumentLine], bool]:
        from docx import Document  # 延迟导入

        doc = Document(str(path))
        lines: list[DocumentLine] = []
        for i, para in enumerate(doc.paragraphs):
            text = para.text.strip()
            if not text:
                continue
            # 用段落序号近似 y 坐标（0-1 区间），保证前端可定位到段落
            y0 = i / max(len(doc.paragraphs), 1)
            y1 = min(1.0, (i + 1) / max(len(doc.paragraphs), 1))
            lines.append(
                DocumentLine(
                    text=text,
                    page=1,
                    bbox=[0.0, y0, 1.0, y1],
                    line_index=i,
                )
            )
        # DOCX 始终有文本层
        return lines, True

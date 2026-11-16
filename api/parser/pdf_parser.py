"""PDF 解析（FR-02）。

优先用 pdfplumber 按行提取文本并保留 `(x0, y0, x1, y1, page)` 坐标，
坐标是前端高亮定位的前提。无文本层（扫描件）时标记 has_text_layer=False，
由上层提示走 OCR 通道（MVP 不实现 OCR）。
"""

from __future__ import annotations

import io
from pathlib import Path

from ..schemas import DocumentLine


class PdfParser:
    """PDF → list[DocumentLine]（含坐标）。"""

    def parse(self, path: str | Path) -> tuple[list[DocumentLine], bool]:
        """返回 (lines, has_text_layer)。"""
        data = Path(path).read_bytes()
        lines: list[DocumentLine] = []
        has_text = False

        try:
            import pdfplumber  # 延迟导入，隔离可选依赖
        except ImportError:  # pragma: no cover
            return self._fallback_pypdf(data), True

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page_no, page in enumerate(pdf.pages, start=1):
                width = page.width or 1
                height = page.height or 1
                text = page.extract_text()
                if text and text.strip():
                    has_text = True
                # 逐词提取以获得精确坐标；回退到逐行
                words = page.extract_words(use_text_flow=False)
                if words:
                    for w in words:
                        lines.append(
                            DocumentLine(
                                text=w.get("text", ""),
                                page=page_no,
                                bbox=_norm_bbox(
                                    w.get("x0", 0),
                                    w.get("top", 0),
                                    w.get("x1", 0),
                                    w.get("bottom", 0),
                                    width,
                                    height,
                                ),
                            )
                        )
                else:
                    # 无 word 层时退化为整行
                    for i, ln in enumerate((text or "").splitlines()):
                        lines.append(
                            DocumentLine(
                                text=ln,
                                page=page_no,
                                bbox=[0.0, i / 50, 1.0, (i + 1) / 50],
                            )
                        )

        # 按词粒度太碎，重建为"行"粒度（同页 y 相近的合并）
        merged = self._merge_words_to_lines(lines)
        return merged, has_text

    # ------------------------------------------------------------------
    @staticmethod
    def _merge_words_to_lines(words: list[DocumentLine], y_tol: float = 0.012) -> list[DocumentLine]:
        """把同页、y 坐标接近的词合并为一行，保留行级 bbox。"""
        if not words:
            return []
        result: list[DocumentLine] = []
        for word in words:
            if result and result[-1].page == word.page:
                prev = result[-1]
                if abs(prev.bbox[1] - word.bbox[1]) <= y_tol:
                    # 合并：文本拼接、bbox 取并集
                    prev.text += word.text
                    prev.bbox[0] = min(prev.bbox[0], word.bbox[0])
                    prev.bbox[1] = min(prev.bbox[1], word.bbox[1])
                    prev.bbox[2] = max(prev.bbox[2], word.bbox[2])
                    prev.bbox[3] = max(prev.bbox[3], word.bbox[3])
                    continue
            result.append(word.model_copy(deep=True))
        return result

    # ------------------------------------------------------------------
    def _fallback_pypdf(self, data: bytes) -> list[DocumentLine]:
        """pdfplumber 不可用时退化为 pypdf 纯文本提取（无坐标）。"""
        from pypdf import PdfReader  # 延迟导入

        reader = PdfReader(io.BytesIO(data))
        lines: list[DocumentLine] = []
        for page_no, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            for i, ln in enumerate(text.splitlines()):
                if ln.strip():
                    lines.append(
                        DocumentLine(
                            text=ln,
                            page=page_no,
                            bbox=[0.0, i / 50, 1.0, (i + 1) / 50],
                        )
                    )
        return lines


def _norm_bbox(x0: float, top: float, x1: float, bottom: float, w: float, h: float) -> list[float]:
    """归一化为 [0,1] 比例坐标，前端无需关心页物理尺寸。"""
    return [
        max(0.0, min(1.0, x0 / w)),
        max(0.0, min(1.0, top / h)),
        max(0.0, min(1.0, x1 / w)),
        max(0.0, min(1.0, bottom / h)),
    ]

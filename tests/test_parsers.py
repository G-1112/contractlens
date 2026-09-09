"""PDF/DOCX 解析器测试（FR-02）。

PDF 用 reportlab 生成含中文文本层的真实 PDF，走 pdfplumber 提取路径；
DOCX 用 python-docx 生成，走段落提取路径。
"""

from __future__ import annotations

import pytest

from api.parser.docx_parser import DocxParser
from api.parser.pdf_parser import PdfParser, _norm_bbox
from api.pipeline import detect_contract_type, parse_file
from api.schemas import DocumentLine


def _make_pdf(path, lines):
    """生成含中文文本层的单页 PDF（STSong-Light CID 字体）。"""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    c = canvas.Canvas(str(path))
    c.setFont("STSong-Light", 12)
    y = 720
    for ln in lines:
        c.drawString(72, y, ln)
        y -= 18
    c.showPage()
    c.save()


def test_pdf_parser_extracts_text(tmp_path):
    p = tmp_path / "a.pdf"
    _make_pdf(p, ["第一条 租赁房屋", "甲方将房屋出租给乙方，押金为三个月租金。"])
    lines, has_text = PdfParser().parse(p)
    assert has_text is True
    assert lines, "应提取到行级文本"
    joined = "".join(ln.text for ln in lines)
    assert "第一条" in joined
    assert "押金" in joined


def test_pdf_parser_no_text_layer(tmp_path):
    from reportlab.pdfgen import canvas

    p = tmp_path / "blank.pdf"
    c = canvas.Canvas(str(p))
    c.showPage()
    c.save()
    lines, has_text = PdfParser().parse(p)
    assert has_text is False


def test_pdf_parser_fallback_pypdf(tmp_path):
    """pdfplumber 不可用时回退 pypdf 纯文本提取（无坐标）。"""
    p = tmp_path / "a.pdf"
    _make_pdf(p, ["第一条 租赁房屋"])
    lines = PdfParser()._fallback_pypdf(p.read_bytes())
    assert any("第一条" in ln.text for ln in lines)


def test_norm_bbox_clamps():
    assert _norm_bbox(-5, -5, 999, 999, 100, 100) == [0.0, 0.0, 1.0, 1.0]
    assert _norm_bbox(10, 20, 30, 40, 100, 100) == [0.1, 0.2, 0.3, 0.4]


def test_merge_words_to_lines_empty():
    assert PdfParser._merge_words_to_lines([]) == []


def test_merge_words_to_lines_same_y():
    from api.parser.pdf_parser import PdfParser as P

    words = [
        DocumentLine(text="第一", page=1, bbox=[0.0, 0.10, 0.2, 0.12]),
        DocumentLine(text="条", page=1, bbox=[0.2, 0.10, 0.3, 0.12]),
        DocumentLine(text="正文", page=1, bbox=[0.0, 0.30, 0.3, 0.32]),
    ]
    merged = P._merge_words_to_lines(words)
    # 前两个 y 接近 → 合并为一行；第三个 y 差距大 → 独立
    assert len(merged) == 2
    assert merged[0].text == "第一条"


def test_docx_parser(tmp_path):
    from docx import Document

    d = Document()
    d.add_paragraph("第一条 合同期限")
    d.add_paragraph("本合同期限为三年。")
    d.add_paragraph("")  # 空段应被跳过
    p = tmp_path / "a.docx"
    d.save(str(p))

    lines, has_text = DocxParser().parse(p)
    assert has_text is True
    assert len(lines) == 2
    assert lines[0].text == "第一条 合同期限"
    assert lines[0].page == 1
    assert lines[0].line_index == 0
    assert lines[1].line_index == 1


def test_parse_file_docx(tmp_path):
    from docx import Document

    d = Document()
    d.add_paragraph("第一条 合同期限")
    d.add_paragraph("本合同期限为三年。")
    p = tmp_path / "a.docx"
    d.save(str(p))

    parsed = parse_file(p, "a.docx", "labor")
    assert parsed.contract_type == "labor"
    assert parsed.has_text_layer is True
    assert parsed.clauses, "应切分出条款树"
    assert parsed.page_count >= 1


def test_parse_file_pdf(tmp_path):
    p = tmp_path / "a.pdf"
    _make_pdf(p, ["第一条 租赁房屋", "甲方将房屋出租给乙方。"])
    parsed = parse_file(p, "a.pdf", "rent")
    assert parsed.contract_type == "rent"
    assert parsed.has_text_layer is True
    assert parsed.clauses


def test_parse_file_unsupported(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        parse_file(p, "a.txt", "rent")


def test_detect_contract_type():
    assert detect_contract_type("劳动合同.docx") == "labor"
    assert detect_contract_type("实习协议.docx") == "intern"
    assert detect_contract_type("房屋租赁.docx") == "rent"
    assert detect_contract_type("unknown.txt") == "rent"

"""DOCX 解析复杂度回归测试（第四轮）。

背景（实测发现）：
`python-docx` 的 `document.paragraphs` 是 **property**，每次访问都会重新遍历
body XML 构造 Paragraph 对象，单次访问为 O(n)。原实现把它写进循环
（`len(doc.paragraphs)` 每轮两次），使整体退化为 O(n²)。

本机同一脚本实测（8000 段正常文档，430KB XML）：

    段数    修复前      修复后     提速
    2000    3.312 s    0.313 s    11x
    4000   27.522 s    0.685 s    40x
    8000  123.023 s    1.480 s    83x

修复前呈 ~4x/倍数的二次增长，修复后为 ~2x 的线性增长。

这同时是一条拒绝服务路径：上传 20MB 的 DOCX（可含上百万段落）会让
/api/analyze 长时间占用 CPU。故此处用「时间上界」而非精确耗时做回归守卫，
阈值留有充足余量以避免在慢机器上误报。
"""

from __future__ import annotations

import time
import zipfile

import pytest

pytest.importorskip("docx", reason="需要 python-docx")

from api.parser.docx_parser import DocxParser  # noqa: E402

_CT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)


def _build_docx(path, n_paras: int) -> None:
    """构造 n 段的合法 DOCX（内容可区分，避免压缩率异常干扰测试）。"""
    body = "".join(
        f"<w:p><w:r><w:t>{i}-abcdefgh-{i * 7919 % 100000}</w:t></w:r></w:p>"
        for i in range(n_paras)
    )
    doc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr("[Content_Types].xml", _CT)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/document.xml", doc)


def test_large_docx_parses_within_time_bound(tmp_path):
    """4000 段文档必须在 10 秒内解析完。

    修复后实测约 0.69 s（余量 14 倍）；若退回 O(n²) 写法，实测约 27.5 s，
    会明确失败。阈值取得宽松，避免慢机器误报。
    """
    p = tmp_path / "big.docx"
    _build_docx(p, 4000)

    t0 = time.perf_counter()
    lines, has_text = DocxParser().parse(p)
    elapsed = time.perf_counter() - t0

    assert has_text is True
    assert len(lines) == 4000
    assert elapsed < 10.0, f"解析 4000 段耗时 {elapsed:.2f}s，疑似退化为 O(n²)"


def test_parse_scales_linearly_not_quadratically(tmp_path):
    """段数翻倍时耗时不应接近 4 倍（二次增长的特征）。"""
    small = tmp_path / "s.docx"
    big = tmp_path / "b.docx"
    _build_docx(small, 2000)
    _build_docx(big, 4000)

    DocxParser().parse(small)  # 预热，避免首次导入/缓存影响

    def timed(path):
        t0 = time.perf_counter()
        DocxParser().parse(path)
        return time.perf_counter() - t0

    t_small = min(timed(small) for _ in range(3))
    t_big = min(timed(big) for _ in range(3))

    # 线性应为 ~2x；二次为 ~4x。取 3.0 作为分界。
    # 用 max(t_small, 1e-3) 防止极小分母导致比值失真。
    ratio = t_big / max(t_small, 1e-3)
    assert ratio < 3.0, (
        f"段数翻倍耗时比 {ratio:.2f}x（t_small={t_small:.4f}s, t_big={t_big:.4f}s），"
        "疑似退化为 O(n²)"
    )


def test_paragraph_property_not_accessed_in_loop():
    """静态守卫：docx_parser 不得在循环体内访问 doc.paragraphs。

    这是 O(n²) 的根源，用源码检查确保不会再次引入。
    """
    import inspect

    from api.parser import docx_parser

    src = inspect.getsource(docx_parser.DocxParser.parse)
    loop_idx = src.find("for i, para in enumerate(paragraphs)")
    assert loop_idx != -1, "解析循环结构已改变，请同步更新本守卫"
    loop_body = src[loop_idx:]
    assert "doc.paragraphs" not in loop_body, (
        "循环体内再次访问 doc.paragraphs（property）会导致 O(n²)，"
        "应先取出列表再复用"
    )


def test_parse_output_unchanged_for_small_doc(tmp_path):
    """修复不得改变输出语义。"""
    p = tmp_path / "s.docx"
    _build_docx(p, 5)
    lines, _ = DocxParser().parse(p)
    assert [ln.line_index for ln in lines] == [0, 1, 2, 3, 4]
    assert all(ln.page == 1 for ln in lines)
    # bbox 归一化到 [0,1] 且单调递增
    for ln in lines:
        assert 0.0 <= ln.bbox[1] < ln.bbox[3] <= 1.0

"""第九轮回归：畸形 DOCX 必须产出 PARSE_FAILED 错误事件，而不是让异常穿透 SSE。

修复前：api/routers/analyze.py 的 event_stream() 只有 try/finally、没有 except，
parse_file() 抛出的 lxml XMLSyntaxError 会直接穿透生成器 —— SSE 流中断且
**不给客户端任何错误事件**，前端随后调 /api/result 拿到 404，
用户只看到「分析失败」却不知道文件已损坏。

实测可复现的三类畸形输入：
  - 控制字符 0x00（Char 0x0 out of allowed range）
  - 未闭合标签（Opening and ending tag mismatch）
  - 深嵌套 XML（Excessive depth in document: 256）
"""

from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)

# TestClient 默认 raise_server_exceptions=True：若异常逃出路由会直接抛到测试里，
# 这正是我们要检测的失败模式。


def _make_docx(body_xml: str) -> bytes:
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument'
        '.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/></Relationships>'
    )
    doc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body_xml}</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc)
    return buf.getvalue()


def _upload(body_xml: str) -> str:
    data = _make_docx(body_xml)
    resp = client.post(
        "/api/upload",
        files={"file": ("t.docx", io.BytesIO(data), "application/octet-stream")},
        data={"contract_type": "rent"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["file_id"]


MALFORMED = {
    "控制字符 0x00": '<w:p><w:r><w:t xml:space="preserve">甲方\x00应支付</w:t></w:r></w:p>',
    "未闭合标签": "<w:p><w:r><w:t>未闭合",
    "深嵌套 XML": "<w:p>" * 1000 + "<w:r><w:t>深</w:t></w:r>" + "</w:p>" * 1000,
}


@pytest.mark.parametrize("name,body", list(MALFORMED.items()))
def test_malformed_docx_yields_parse_failed_event(name, body):
    """畸形 DOCX：必须返回 200 + PARSE_FAILED 事件，且不泄漏内部细节。"""
    file_id = _upload(body)
    resp = client.post("/api/analyze", json={"file_id": file_id, "contract_type": "rent"})
    assert resp.status_code == 200, f"{name}: HTTP {resp.status_code}"

    text = resp.text
    assert "PARSE_FAILED" in text, f"{name}: 未产出 PARSE_FAILED 事件\n{text[:300]}"
    # 安全：不得把内部路径/堆栈回传客户端（沿用 R-ERR-01）
    for leaked in ("Traceback", "E:\\", "C:\\", "site-packages"):
        assert leaked not in text, f"{name}: 响应泄漏内部细节 {leaked}"

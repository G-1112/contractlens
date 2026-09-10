"""上传安全回归测试（本轮审计新增）：内容签名校验 + 限流。

对应两个真实风险：
  1. 扩展名可伪造 —— 把任意文件改名 .docx 即可绕过类型白名单，
     触发解析器处理非预期格式；
  2. 无速率限制 —— 上传/分析接口可被高频调用，耗尽磁盘、内存与 LLM 配额。
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from api.config import get_settings
from api.main import app
from api.ratelimit import analyze_limiter, upload_limiter


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """隔离上传目录与限流计数，保证用例互不干扰。"""
    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path)
    upload_limiter.reset()
    analyze_limiter.reset()
    yield TestClient(app)
    upload_limiter.reset()
    analyze_limiter.reset()


def _docx_bytes() -> bytes:
    from docx import Document

    d = Document()
    d.add_paragraph("第一条 合同期限")
    d.add_paragraph("本合同期限为三年。")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 内容签名（magic bytes）
# ---------------------------------------------------------------------------
def test_fake_docx_rejected_by_magic(client):
    """扩展名 .docx 但内容是纯文本 → 415，不得进入解析流程。"""
    r = client.post(
        "/api/upload",
        files={"file": ("fake.docx", b"hello world", "application/octet-stream")},
    )
    assert r.status_code == 415
    assert "不符" in r.json()["detail"] or "伪装" in r.json()["detail"]


def test_fake_pdf_rejected_by_magic(client):
    """扩展名 .pdf 但内容不是 %PDF- → 415。"""
    r = client.post(
        "/api/upload",
        files={"file": ("fake.pdf", b"PK\x03\x04not a pdf", "application/octet-stream")},
    )
    assert r.status_code == 415


def test_real_docx_accepted(client):
    r = client.post(
        "/api/upload",
        files={"file": ("real.docx", _docx_bytes(), "application/octet-stream")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["has_text_layer"] is True


def test_real_pdf_accepted(client):
    """合法 PDF 签名 %PDF- 应被接受（内容本身可解析失败，但签名校验需放行）。"""
    r = client.post(
        "/api/upload",
        files={"file": ("a.pdf", b"%PDF-1.4\n%fake body", "application/pdf")},
    )
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# 限流
# ---------------------------------------------------------------------------
def test_upload_rate_limited(client):
    """连续上传超过阈值（10 次/分钟）后返回 429。"""
    data = _docx_bytes()
    codes = [
        client.post(
            "/api/upload",
            files={"file": (f"a{i}.docx", data, "application/octet-stream")},
        ).status_code
        for i in range(12)
    ]
    assert codes[:10] == [200] * 10, f"前 10 次应放行，实际 {codes}"
    assert 429 in codes, f"超限后应限流，实际 {codes}"


def test_rate_limit_retry_after_hint(client):
    """429 响应应给出可重试的秒数提示。"""
    data = _docx_bytes()
    for i in range(11):
        r = client.post(
            "/api/upload",
            files={"file": (f"b{i}.docx", data, "application/octet-stream")},
        )
    assert r.status_code == 429
    assert "秒后再试" in r.json()["detail"]


def test_rate_limit_recovers_after_reset(client):
    """限流窗口重置后应恢复放行（验证不是永久封禁）。"""
    data = _docx_bytes()
    for i in range(12):
        client.post(
            "/api/upload",
            files={"file": (f"c{i}.docx", data, "application/octet-stream")},
        )
    upload_limiter.reset()
    r = client.post(
        "/api/upload", files={"file": ("d.docx", data, "application/octet-stream")}
    )
    assert r.status_code == 200


def test_analyze_rate_limited(client):
    """分析接口同样受限（LLM 调用有真实成本）。"""
    codes = [
        client.post("/api/analyze", json={"file_id": f"ghost{i}", "contract_type": "rent"}).status_code
        for i in range(25)
    ]
    # 前 20 次为 404（文件不存在），超限后转为 429
    assert codes[:20] == [404] * 20, codes[:20]
    assert 429 in codes, f"超限后应限流，实际 {codes[-5:]}"

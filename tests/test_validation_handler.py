"""请求校验错误处理（R-ERR-01）。

二进制 body POST 到 JSON 端点曾打挂 FastAPI 默认校验处理器
（jsonable_encoder 对 bytes 调 decode() → UnicodeDecodeError → 500）。
修复后必须：
  1. 返回 422 而非 500；
  2. 错误详情不回显原始请求体，只给长度占位符。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.main import app


def test_binary_body_to_json_endpoint_returns_422():
    # 复现路径：content-type 不是 application/json 时，FastAPI 会把原始请求体
    # 字节直接喂给 Pydantic 模型（curl -F 上传到 /api/analyze 即此类），
    # 默认处理器 jsonable_encoder(bytes) 崩 UnicodeDecodeError → 500。
    client = TestClient(app)
    body = b"PK\x03\x04\x14\x00\x00\x00\x08\x00binary-docx-garbage\xf7\x8d"
    r = client.post(
        "/api/analyze",
        content=body,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert r.status_code == 422, f"二进制 body 应走 422 校验失败，实际 {r.status_code}"
    detail = r.json()["detail"]
    assert isinstance(detail, list) and detail, "detail 应为非空列表"
    # 不回显原始二进制：既防信息泄露，也防响应二次编码时再崩
    assert body not in r.content
    assert "<binary" in r.text


def test_normal_json_validation_still_returns_422_with_field_loc():
    """正常 JSON（缺字段/类型错）仍走 422，且保留 loc 定位信息。"""
    client = TestClient(app)
    r = client.post("/api/analyze", json={"contract_type": "loan"})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert any("file_id" in str(e.get("loc", ())) for e in detail)

"""示例合同接口（评审零门槛体验）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_sample_returns_docx(client: TestClient):
    r = client.get("/api/sample/loan")
    assert r.status_code == 200
    ct = r.headers.get("content-type", "")
    assert "wordprocessingml" in ct
    assert len(r.content) > 1000  # 真实 docx，不是占位


def test_sample_unknown_404(client: TestClient):
    assert client.get("/api/sample/not-exist").status_code == 404


def test_sample_path_traversal_blocked(client: TestClient):
    # key 只从白名单字典取值，路径跳转字符不可能命中任何 key。
    # 部分输入会被框架归一化后落到 SPA 兜底（200 返回 index.html），
    # 但无论返回什么，示例接口都绝不能以 docx 响应、绝不能泄出环境内容。
    for bad in ("..", "..%2F..%2F.env", "%2e%2e%2fmain", "..%2f..%2f..%2f"):
        r = client.get(f"/api/sample/{bad}")
        ct = r.headers.get("content-type", "")
        assert "wordprocessingml" not in ct
        assert "REVIEW_ACCESS_CODE" not in r.text


def test_sample_all_whitelist_keys_exist(client: TestClient):
    for key in ("loan", "loan_injection", "rent", "labor", "intern", "guarantee"):
        r = client.get(f"/api/sample/{key}")
        assert r.status_code == 200, f"{key} 示例缺失"

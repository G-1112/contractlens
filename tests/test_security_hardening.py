"""第三轮安全加固测试（响应头 / 限流补全 / 错误信息防泄露）。

对应审计发现：
  - R-HEAD-01：缺少 X-Content-Type-Options / X-Frame-Options / CSP 等安全响应头；
  - R-RL-01  ：/api/script 与 /api/report 此前无速率限制；
  - R-ERR-01 ：分析异常原文（可能含内部路径）经 SSE 回传客户端。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.ratelimit import report_limiter, script_limiter


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_limiters():
    script_limiter.reset()
    report_limiter.reset()
    yield
    script_limiter.reset()
    report_limiter.reset()


# ---------------------------------------------------------------- 安全响应头
def test_security_headers_present_on_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    h = r.headers
    assert h.get("x-content-type-options") == "nosniff"
    assert h.get("x-frame-options") == "DENY"
    assert h.get("referrer-policy") == "strict-origin-when-cross-origin"
    assert "content-security-policy" in h
    assert "frame-ancestors 'none'" in h["content-security-policy"]


def test_csp_blocks_object_and_framing(client):
    csp = client.get("/api/health").headers["content-security-policy"]
    assert "object-src 'none'" in csp
    assert "default-src 'self'" in csp


def test_hsts_only_on_https(client):
    """本地 http 开发不应下发 HSTS，避免把开发环境锁死到 https。"""
    assert "strict-transport-security" not in client.get("/api/health").headers


def test_security_headers_present_on_error_response(client):
    """404 等错误响应同样要带安全头（中间件在出站统一注入）。"""
    r = client.get("/api/result/not-exist")
    assert r.status_code == 404
    assert r.headers.get("x-content-type-options") == "nosniff"


# ---------------------------------------------------------------- 限流补全
def test_script_endpoint_is_rate_limited(client):
    """/api/script 触发真实 LLM 调用，超过 15 次/分必须 429。"""
    payload = {"file_id": "no-such-file", "clause_ids": []}
    codes = [client.post("/api/script", json=payload).status_code for _ in range(17)]
    assert 429 in codes, f"脚本接口未被限流：{codes}"
    # 限流后应带 Retry-After
    last = client.post("/api/script", json=payload)
    assert last.status_code == 429
    assert "retry-after" in {k.lower() for k in last.headers}


def test_report_endpoint_is_rate_limited(client):
    """/api/report 走 WeasyPrint 重渲染，超过 20 次/分必须 429。"""
    codes = [client.post("/api/report/no-such-file?format=md").status_code for _ in range(22)]
    assert 429 in codes, f"报告接口未被限流：{codes}"


def test_rate_limit_returns_retry_after_header(client):
    for _ in range(16):
        client.post("/api/script", json={"file_id": "x", "clause_ids": []})
    r = client.post("/api/script", json={"file_id": "x", "clause_ids": []})
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 1


def test_limiter_reset_restores_access(client):
    for _ in range(16):
        client.post("/api/script", json={"file_id": "x", "clause_ids": []})
    assert client.post("/api/script", json={"file_id": "x", "clause_ids": []}).status_code == 429

    script_limiter.reset()
    # 重置后应重新返回业务错误（404）而非 429
    r = client.post("/api/script", json={"file_id": "x", "clause_ids": []})
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------- 错误信息
def test_analyze_error_message_is_sanitized():
    """SSE error 事件不得回传异常原文（防内部路径泄露）。"""
    from api.routers import analyze as analyze_mod
    import inspect

    src = inspect.getsource(analyze_mod)
    # 断言错误载荷不含 str(e) 直接回传
    assert '"message": str(e)' not in src, "异常原文仍被回传客户端"
    assert "logger.exception" in src, "异常未落服务端日志"

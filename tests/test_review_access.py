"""评审访问门禁（R-AUTH-01）。

未设置 REVIEW_ACCESS_CODE 时必须完全不影响既有行为（本地开发/既有测试
都跑在这个分支），设置后业务端点 401、健康检查与静态资源放行。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.config import get_settings
from api.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_gate_disabled_by_default(client: TestClient):
    """默认（未配置口令）时业务端点不 401 —— 保证向后兼容。"""
    assert get_settings().review_access_code == ""
    r = client.get("/api/result/nonexistent")
    assert r.status_code == 404, "门禁关闭时应走原业务逻辑（结果不存在 → 404）"


def test_gate_blocks_without_code(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(get_settings(), "review_access_code", "t0ps3cret")
    try:
        r = client.get("/api/result/whatever")
        assert r.status_code == 401
        assert "评审访问口令" in r.json()["detail"]

        # 错误口令同样拒绝
        r2 = client.get("/api/result/whatever", headers={"X-Review-Code": "wrong"})
        assert r2.status_code == 401
    finally:
        monkeypatch.setattr(get_settings(), "review_access_code", "")


def test_gate_accepts_header_and_query(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(get_settings(), "review_access_code", "t0ps3cret")
    try:
        # 正确头 → 进入业务逻辑（404 而非 401）
        assert client.get("/api/result/x", headers={"X-Review-Code": "t0ps3cret"}).status_code == 404
        # 正确查询参数同样放行（方便浏览器直接打开导出链接）
        assert client.get("/api/result/x", params={"code": "t0ps3cret"}).status_code == 404
    finally:
        monkeypatch.setattr(get_settings(), "review_access_code", "")


def test_health_exempt_under_gate(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """健康检查必须始终放行——部署平台的可用性探测依赖它。"""
    monkeypatch.setattr(get_settings(), "review_access_code", "t0ps3cret")
    try:
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
    finally:
        monkeypatch.setattr(get_settings(), "review_access_code", "")


def test_health_reports_access_gate_flag(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """前端据此判断是否先显示口令页，字段必须与配置同步。"""
    assert client.get("/api/health").json()["access_gate"] is False
    monkeypatch.setattr(get_settings(), "review_access_code", "t0ps3cret")
    try:
        assert client.get("/api/health").json()["access_gate"] is True
    finally:
        monkeypatch.setattr(get_settings(), "review_access_code", "")

# ---------------------------------------------------------------------------
# 登录接口（第十四轮，区域赛升级 · R-AUTH-02）
# ---------------------------------------------------------------------------

def test_login_success_returns_token(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """账号+密码正确 → 200，返回签名会话令牌（第十五轮起不再是明文口令）。"""
    import api.main as _main
    from api.ratelimit import login_limiter

    _main._LOGIN_FAILS.clear()
    login_limiter._hits.clear()
    s = get_settings()
    monkeypatch.setattr(s, "review_access_code", "t0ps3cret")
    monkeypatch.setattr(s, "review_username", "reviewer")
    r = client.post("/api/login", json={"username": "reviewer", "password": "t0ps3cret"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["token"] != "t0ps3cret"  # 第十五轮：会话令牌，非明文口令
    assert "." in data["token"]
    assert data["expires_in"] == s.review_session_ttl
    # 会话令牌直接用作门禁头
    assert client.get("/api/result/x", headers={"X-Review-Session": data["token"]}).status_code == 404


def test_login_wrong_password_401(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    s = get_settings()
    monkeypatch.setattr(s, "review_access_code", "t0ps3cret")
    r = client.post("/api/login", json={"username": "reviewer", "password": "wrong"})
    assert r.status_code == 401
    assert "不正确" in r.json()["detail"]


def test_login_wrong_username_401(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    s = get_settings()
    monkeypatch.setattr(s, "review_access_code", "t0ps3cret")
    r = client.post("/api/login", json={"username": "hacker", "password": "t0ps3cret"})
    assert r.status_code == 401


def test_login_exempt_from_gate(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """登录接口本身不能被门禁拦住（否则永远进不去）。"""
    monkeypatch.setattr(get_settings(), "review_access_code", "t0ps3cret")
    r = client.post("/api/login", json={"username": "reviewer", "password": "bad"})
    assert r.status_code == 401  # 是业务 401（密码错），不是门禁 401 语义上的不可达


def test_login_lockout_after_repeated_failures(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """连续失败 5 次 → 第 6 次返回 429（爆破防护）。"""
    import api.main as _main
    from api.ratelimit import login_limiter

    _main._LOGIN_FAILS.clear()  # 其它用例可能已为同一测试 IP 累计失败计数
    login_limiter._hits.clear()
    s = get_settings()
    monkeypatch.setattr(s, "review_access_code", "t0ps3cret")
    monkeypatch.setattr(s, "review_username", "reviewer")
    for _ in range(5):
        assert client.post(
            "/api/login", json={"username": "reviewer", "password": "wrong"}
        ).status_code == 401
    r = client.post("/api/login", json={"username": "reviewer", "password": "t0ps3cret"})
    assert r.status_code == 429


# ---------------------------------------------------------------------------
# 会话令牌（第十五轮，R-AUTH-03）
# ---------------------------------------------------------------------------

def test_login_returns_signed_session(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """登录返回的 token 应是签名会话令牌（含 . 分隔的 payload+签名）。"""
    import api.main as _main
    from api.ratelimit import login_limiter

    _main._LOGIN_FAILS.clear()
    login_limiter._hits.clear()
    s = get_settings()
    monkeypatch.setattr(s, "review_access_code", "t0ps3cret")
    r = client.post("/api/login", json={"username": "reviewer", "password": "t0ps3cret"})
    assert r.status_code == 200
    token = r.json()["token"]
    assert "." in token and token != "t0ps3cret"  # 不再是明文口令
    # 会话头可直接过门禁
    assert client.get("/api/result/x", headers={"X-Review-Session": token}).status_code == 404


def test_session_token_tampered_rejected(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    s = get_settings()
    monkeypatch.setattr(s, "review_access_code", "t0ps3cret")
    r = client.post("/api/login", json={"username": "reviewer", "password": "t0ps3cret"})
    token = r.json()["token"]
    bad = token[:-4] + ("0000" if not token.endswith("0000") else "1111")
    assert client.get("/api/result/x", headers={"X-Review-Session": bad}).status_code == 401


def test_session_token_expired_rejected(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    import api.main as _main

    s = get_settings()
    monkeypatch.setattr(s, "review_access_code", "t0ps3cret")
    expired = _main._session_sign({"u": "reviewer", "e": 1})  # 1970 年过期
    assert client.get("/api/result/x", headers={"X-Review-Session": expired}).status_code == 401


def test_legacy_code_still_accepted(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """?code= 直链与 X-Review-Code 旧通道必须保持兼容（导出链接依赖）。"""
    monkeypatch.setattr(get_settings(), "review_access_code", "t0ps3cret")
    assert client.get("/api/result/x", headers={"X-Review-Code": "t0ps3cret"}).status_code == 404
    assert client.get("/api/result/x", params={"code": "t0ps3cret"}).status_code == 404

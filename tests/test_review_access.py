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
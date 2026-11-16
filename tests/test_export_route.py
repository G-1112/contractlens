"""导出接口的 HTTP 方法契约与降级行为（P0/P1 回归）。

背景（2026-11-17 评审核对）：
- 前端导出按钮是 `<a href>`，浏览器发 GET；后端早期只注册 POST，
  四个导出入口（Markdown / 修订稿 / 整改清单 / PDF）全部返回 405。
  本文件用 GET 请求直接锁死该契约，避免回归。
- PDF 导出依赖 WeasyPrint（需 GTK/Pango）。本机未装时 save_pdf 会抛
  PdfRenderUnavailable，路由必须返回 503 + 可操作文案，而不是 500 堆栈。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.config import get_settings
from api.main import app
from api.report import generator as generator_mod
from api.report.generator import PdfRenderUnavailable
from api.schemas import AnalysisReport, RiskItem
from api.store import store

FILE_ID = "exporttest01"


def _report() -> AnalysisReport:
    items = [
        RiskItem(
            clause_id="c1",
            clause_text="第五条 押金概不退还",
            final_level="高",
            risk_type="押金",
            explain="押金一律不退，缺少退还条件",
            suggest="补充退还条件与时限",
            rewrite="租赁期满且房屋无损坏的，押金七日内无息退还",
            source="both",
        )
    ]
    return AnalysisReport(
        file_id=FILE_ID,
        contract_type="rent",
        clause_count=1,
        items=items,
        total_score=60,
        overall_level="中",
        radar={d: 70 for d in ("押金", "解约", "维修", "违约", "隐私")},
        radar_dimensions=["押金", "解约", "维修", "违约", "隐私"],
        talk_scripts=["您好，关于第五条……"],
        injection_flagged=False,
        truncated=False,
        disclaimer="本结果由 AI 辅助生成，不构成法律意见，仅供参考。",
    )


@pytest.fixture()
def client(tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "upload_dir", tmp_path)
    from api.ratelimit import report_limiter

    report_limiter.reset()
    for attr in ("uploads", "documents", "reports", "_upload_ts", "_doc_ts", "_report_ts"):
        setattr(store, attr, {})
    store.put_report(FILE_ID, _report())
    yield TestClient(app)
    report_limiter.reset()
    for attr in ("uploads", "documents", "reports", "_upload_ts", "_doc_ts", "_report_ts"):
        setattr(store, attr, {})


@pytest.mark.parametrize("fmt", ["md", "revised", "checklist"])
def test_export_get_returns_markdown(client, fmt):
    """GET（浏览器 <a href> 的真实行为）必须可用——修复前这里是 405。"""
    r = client.get(f"/api/report/{FILE_ID}?format={fmt}")
    assert r.status_code == 200, r.text
    assert "text/markdown" in r.headers["content-type"]
    assert len(r.text) > 50


def test_export_post_still_supported(client):
    """POST 保留兼容（既有脚本/调用方）。"""
    r = client.post(f"/api/report/{FILE_ID}?format=md")
    assert r.status_code == 200, r.text


def test_export_unknown_format_400(client):
    r = client.get(f"/api/report/{FILE_ID}?format=xlsx")
    assert r.status_code == 400
    assert "md" in r.json()["detail"]


def test_export_missing_report_404(client):
    r = client.get("/api/report/notexist?format=md")
    assert r.status_code == 404


def test_pdf_unavailable_returns_503_with_actionable_hint(client, monkeypatch):
    """缺 WeasyPrint/GTK 时：503 + 明确指引，而不是 500 堆栈。"""

    def _boom(self, out):  # noqa: ANN001
        raise PdfRenderUnavailable("当前环境缺少 PDF 渲染依赖（WeasyPrint 需要 GTK/Pango）。请改用「导出 Markdown」")

    monkeypatch.setattr(generator_mod.ReportGenerator, "save_pdf", _boom, raising=True)
    r = client.get(f"/api/report/{FILE_ID}?format=pdf")
    assert r.status_code == 503, r.text
    detail = r.json()["detail"]
    assert "GTK" in detail or "WeasyPrint" in detail
    assert "Markdown" in detail


def test_save_pdf_raises_domain_error_when_dependency_missing():
    """save_pdf 在导入期缺依赖时应抛领域异常，而非 ImportError/OSError 裸奔。"""
    import builtins
    import sys

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        if name == "weasyprint":
            raise OSError("cannot load library 'libgobject-2.0-0'")
        return real_import(name, *args, **kwargs)

    monkeypatch_target = sys.modules.pop("weasyprint", None)
    builtins.__import__ = _fake_import
    try:
        with pytest.raises(PdfRenderUnavailable):
            generator_mod.ReportGenerator(_report()).save_pdf("/tmp/x.pdf")  # noqa: S108
    finally:
        builtins.__import__ = real_import
        if monkeypatch_target is not None:
            sys.modules["weasyprint"] = monkeypatch_target


def test_docx_export_available(client):
    """DOCX 只依赖 python-docx，应能正常产出（不依赖 GTK）。"""
    r = client.get(f"/api/report/{FILE_ID}?format=docx")
    assert r.status_code == 200, r.text
    assert r.content[:2] == b"PK"  # docx 是 zip 包

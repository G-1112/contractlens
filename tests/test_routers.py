"""FastAPI 路由集成测试：上传 → 分析 → 结果 → 报告 → 话术 全链路。

用 TestClient 直连 app，覆盖 main / upload / analyze / report / script 五个模块，
以及 404/415/400 等错误分支。分析流程在 mock 模式（无 LLM_API_KEY）下离线跑通。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.config import get_settings
from api.main import app
from api.schemas import AnalysisReport, RiskItem
from api.store import store


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """隔离：上传目录指向临时目录，并清空全局 store 单例。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "upload_dir", tmp_path)
    _reset_store()
    yield TestClient(app)
    _reset_store()


def _reset_store() -> None:
    for attr in ("uploads", "documents", "reports", "_upload_ts", "_doc_ts", "_report_ts"):
        setattr(store, attr, {})


def _make_docx(path):
    from docx import Document

    d = Document()
    d.add_paragraph("第一条 租赁房屋")
    d.add_paragraph("甲方将房屋出租给乙方，押金为三个月租金。")
    d.add_paragraph("第四条 押金概不退还。")
    d.save(str(path))


def _upload_docx(client, tmp_path, contract_type="rent"):
    docx = tmp_path / "contract.docx"
    _make_docx(docx)
    with open(docx, "rb") as f:
        r = client.post(
            "/api/upload",
            files={"file": ("contract.docx", f, "application/octet-stream")},
            data={"contract_type": contract_type},
        )
    return r


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_root(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "契约眼" in r.json()["service"]


# ---------------------------------------------------------------------------
# upload
# ---------------------------------------------------------------------------
def test_upload_docx(client, tmp_path):
    r = _upload_docx(client, tmp_path)
    assert r.status_code == 200
    data = r.json()
    assert data["contract_type"] == "rent"
    assert data["has_text_layer"] is True
    assert data["page_count"] >= 1
    assert data["file_id"]


def test_upload_invalid_contract_type_fallback(client, tmp_path):
    r = _upload_docx(client, tmp_path, contract_type="hack")
    assert r.status_code == 200
    assert r.json()["contract_type"] == "rent"  # 未知类型兜底 rent


def test_upload_reject_extension(client):
    r = client.post("/api/upload", files={"file": ("x.txt", b"hello", "text/plain")})
    assert r.status_code == 415


def test_upload_empty_file(client):
    r = client.post("/api/upload", files={"file": ("x.docx", b"", "application/octet-stream")})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------
def test_analyze_full_flow(client, tmp_path):
    up = _upload_docx(client, tmp_path)
    fid = up.json()["file_id"]

    r = client.post("/api/analyze", json={"file_id": fid, "contract_type": "rent"})
    assert r.status_code == 200
    assert "data:" in r.text  # SSE 事件流

    # 分析完成后报告应写入 store（上传已按 NFR-05 删除）
    rep = store.get_report(fid)
    assert rep is not None
    assert rep.file_id == fid
    assert store.get_upload(fid) is None  # 分析完成即删


def test_analyze_missing_file(client):
    r = client.post("/api/analyze", json={"file_id": "nope", "contract_type": "rent"})
    assert r.status_code == 404


def test_analyze_no_text_layer_pdf(client, tmp_path):
    """扫描版 PDF（无文本层）→ analyze 返回 NO_TEXT_LAYER 错误事件。"""
    from reportlab.pdfgen import canvas

    pdf = tmp_path / "scan.pdf"
    c = canvas.Canvas(str(pdf))
    c.showPage()
    c.save()

    with open(pdf, "rb") as f:
        up = client.post(
            "/api/upload", files={"file": ("scan.pdf", f, "application/pdf")}
        )
    assert up.status_code == 200
    fid = up.json()["file_id"]

    r = client.post("/api/analyze", json={"file_id": fid, "contract_type": "rent"})
    assert r.status_code == 200
    assert "NO_TEXT_LAYER" in r.text


def test_get_document_missing(client):
    r = client.get("/api/document/nope")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
def _put_report(fid="f1") -> AnalysisReport:
    rep = AnalysisReport(
        file_id=fid,
        contract_type="rent",
        total_score=80,
        items=[
            RiskItem(
                clause_id="c1",
                clause_text="第五条 押金概不退还",
                final_level="高",
                source="both",
                explain="押金不退",
                suggest="建议明确退还条件",
                rewrite="租赁期满押金无息退还",
            )
        ],
        clause_count=1,
        talk_scripts=["您好，关于押金这一项……"],
    )
    store.put_report(fid, rep)
    return rep


def test_get_result_missing(client):
    r = client.get("/api/result/nope")
    assert r.status_code == 404


def test_get_result(client):
    rep = _put_report("f1")
    r = client.get("/api/result/f1")
    assert r.status_code == 200
    assert r.json()["file_id"] == "f1"
    assert r.json()["total_score"] == rep.total_score


def test_export_markdown(client):
    _put_report("f1")
    r = client.post("/api/report/f1", params={"format": "md"})
    assert r.status_code == 200
    assert "合同审查报告" in r.text


def test_export_revised(client):
    """修订稿导出：原文 ↔ 改写后对照（交互增强）。"""
    _put_report("f1")
    r = client.post("/api/report/f1", params={"format": "revised"})
    assert r.status_code == 200
    assert "合同修订建议稿" in r.text
    assert "建议改为" in r.text
    assert "attachment" in r.headers.get("content-disposition", "")


def test_export_docx(client):
    _put_report("f1")
    r = client.post("/api/report/f1", params={"format": "docx"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


def test_export_invalid_format(client):
    _put_report("f1")
    r = client.post("/api/report/f1", params={"format": "xml"})
    assert r.status_code == 400


def test_export_missing_report(client):
    r = client.post("/api/report/nope", params={"format": "md"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# script
# ---------------------------------------------------------------------------
def test_script_returns_existing_talk_scripts(client):
    rep = _put_report("f1")
    r = client.post("/api/script", json={"file_id": "f1"})
    assert r.status_code == 200
    assert r.json()["scripts"] == rep.talk_scripts


def test_script_with_clause_ids(client):
    _put_report("f1")
    r = client.post("/api/script", json={"file_id": "f1", "clause_ids": ["c1"]})
    assert r.status_code == 200
    assert len(r.json()["scripts"]) >= 1


def test_script_unknown_clause_falls_back_to_high_medium(client):
    _put_report("f1")
    # 未知 clause_id → 回退到高/中风险条款
    r = client.post("/api/script", json={"file_id": "f1", "clause_ids": ["nope"]})
    assert r.status_code == 200
    assert len(r.json()["scripts"]) >= 1


def test_script_missing_report(client):
    r = client.post("/api/script", json={"file_id": "nope"})
    assert r.status_code == 404

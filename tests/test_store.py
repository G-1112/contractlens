"""store.py 内存存储与 TTL 过期清理测试（审计 P2：内存只增不减）。"""

from __future__ import annotations

from api.schemas import AnalysisReport, ParsedDocument
from api.store import _Store


def _make_store(ttl: float = 3600.0) -> _Store:
    return _Store(ttl_seconds=ttl)


def _doc() -> ParsedDocument:
    return ParsedDocument(lines=[], clauses=[], page_count=1, contract_type="rent")


def test_add_and_get_upload(tmp_path):
    s = _make_store()
    p = tmp_path / "a.docx"
    p.write_text("x", encoding="utf-8")
    s.add_upload("f1", p, "a.docx", "rent")
    e = s.get_upload("f1")
    assert e is not None
    assert e["filename"] == "a.docx"
    assert e["contract_type"] == "rent"
    assert e["path"] == p


def test_get_upload_missing():
    s = _make_store()
    assert s.get_upload("nope") is None


def test_remove_upload_unlinks_file(tmp_path):
    s = _make_store()
    p = tmp_path / "a.docx"
    p.write_text("x", encoding="utf-8")
    s.add_upload("f1", p, "a.docx", "rent")
    assert p.exists()
    s.remove_upload("f1")
    assert s.get_upload("f1") is None
    assert not p.exists()


def test_remove_upload_missing_is_safe():
    s = _make_store()
    s.remove_upload("ghost")  # 不应抛异常


def test_document_roundtrip():
    s = _make_store()
    doc = _doc()
    s.put_document("f1", doc)
    assert s.get_document("f1") is doc
    assert s.get_document("ghost") is None


def test_report_roundtrip():
    s = _make_store()
    r = AnalysisReport(file_id="f1", contract_type="rent")
    s.put_report("f1", r)
    assert s.get_report("f1") is r
    assert s.get_report("ghost") is None


def test_document_ttl_expiry(monkeypatch):
    s = _make_store(ttl=10.0)
    clock = {"now": 1000.0}
    monkeypatch.setattr(s, "_now", lambda: clock["now"])

    s.put_document("f1", _doc())  # _doc_ts[f1] = 1000.0
    assert s.get_document("f1") is not None  # 未过期

    clock["now"] = 1011.0  # 越过 TTL
    assert s.get_document("f1") is None


def test_report_ttl_expiry(monkeypatch):
    s = _make_store(ttl=10.0)
    clock = {"now": 1000.0}
    monkeypatch.setattr(s, "_now", lambda: clock["now"])

    s.put_report("f1", AnalysisReport(file_id="f1"))
    assert s.get_report("f1") is not None

    clock["now"] = 1011.0
    assert s.get_report("f1") is None


def test_upload_ttl_expiry_unlinks(monkeypatch, tmp_path):
    s = _make_store(ttl=10.0)
    clock = {"now": 1000.0}
    monkeypatch.setattr(s, "_now", lambda: clock["now"])

    p = tmp_path / "a.docx"
    p.write_text("x", encoding="utf-8")
    s.add_upload("f1", p, "a.docx", "rent")
    assert p.exists()

    clock["now"] = 1011.0
    assert s.get_upload("f1") is None
    assert not p.exists()  # 过期上传同时清理落盘文件


def test_cleanup_expired_counts(monkeypatch, tmp_path):
    s = _make_store(ttl=10.0)
    clock = {"now": 1000.0}
    monkeypatch.setattr(s, "_now", lambda: clock["now"])

    p = tmp_path / "a.docx"
    p.write_text("x", encoding="utf-8")
    s.add_upload("f1", p, "a.docx", "rent")
    s.put_document("f1", _doc())
    s.put_report("f1", AnalysisReport(file_id="f1"))

    clock["now"] = 1011.0
    removed = s.cleanup_expired()
    assert removed == 3
    assert s.get_upload("f1") is None
    assert s.get_document("f1") is None
    assert s.get_report("f1") is None


def test_cleanup_nothing_when_fresh():
    s = _make_store(ttl=3600.0)
    s.put_report("f1", AnalysisReport(file_id="f1"))
    assert s.cleanup_expired() == 0
    assert s.get_report("f1") is not None

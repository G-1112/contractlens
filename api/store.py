"""内存态存储（单进程演示用，不落盘持久化，符合隐私要求 NFR-05）。

- 上传文件：临时落盘到 upload_dir，分析完成后立即删除；
- 分析结果：内存字典，GET /result/{file_id} 可复取。
"""

from __future__ import annotations

from pathlib import Path

from .schemas import AnalysisReport, ParsedDocument


class _Store:
    def __init__(self) -> None:
        self.uploads: dict[str, dict] = {}  # file_id -> {path, filename, contract_type}
        self.documents: dict[str, ParsedDocument] = {}  # file_id -> ParsedDocument
        self.reports: dict[str, AnalysisReport] = {}  # file_id -> AnalysisReport

    # ---- 上传 ----
    def add_upload(self, file_id: str, path: Path, filename: str, contract_type: str) -> None:
        self.uploads[file_id] = {
            "path": path,
            "filename": filename,
            "contract_type": contract_type,
        }

    def get_upload(self, file_id: str) -> dict | None:
        return self.uploads.get(file_id)

    def remove_upload(self, file_id: str) -> None:
        entry = self.uploads.pop(file_id, None)
        if entry:
            try:
                entry["path"].unlink(missing_ok=True)
            except OSError:
                pass

    # ---- 解析/结果 ----
    def put_document(self, file_id: str, doc: ParsedDocument) -> None:
        self.documents[file_id] = doc

    def get_document(self, file_id: str) -> ParsedDocument | None:
        return self.documents.get(file_id)

    def put_report(self, file_id: str, report: AnalysisReport) -> None:
        self.reports[file_id] = report

    def get_report(self, file_id: str) -> AnalysisReport | None:
        return self.reports.get(file_id)


store = _Store()

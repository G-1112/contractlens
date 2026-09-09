"""内存态存储（单进程演示用，不落盘持久化，符合隐私要求 NFR-05）。

- 上传文件：临时落盘到 upload_dir，分析完成后立即删除；
- 分析结果：内存字典，GET /result/{file_id} 可复取；
- 内置 TTL 过期清理，避免长时间运行内存只增不减（审计 P2）。

注意：本存储为单进程内存态，部署必须单 worker（见 README「部署约束」）。
"""

from __future__ import annotations

import time
from pathlib import Path

from .schemas import AnalysisReport, ParsedDocument


class _Store:
    def __init__(self, ttl_seconds: float = 3600.0) -> None:
        self.ttl_seconds = ttl_seconds
        self.uploads: dict[str, dict] = {}  # file_id -> {path, filename, contract_type}
        self.documents: dict[str, ParsedDocument] = {}  # file_id -> ParsedDocument
        self.reports: dict[str, AnalysisReport] = {}  # file_id -> AnalysisReport
        # 各分区的写入时间戳（TTL 清理依据）
        self._upload_ts: dict[str, float] = {}
        self._doc_ts: dict[str, float] = {}
        self._report_ts: dict[str, float] = {}

    @staticmethod
    def _now() -> float:
        return time.time()

    def _expired(self, ts: float | None) -> bool:
        return ts is not None and (self._now() - ts) > self.ttl_seconds

    # ---- 上传 ----
    def add_upload(self, file_id: str, path: Path, filename: str, contract_type: str) -> None:
        self.uploads[file_id] = {
            "path": path,
            "filename": filename,
            "contract_type": contract_type,
        }
        self._upload_ts[file_id] = self._now()

    def get_upload(self, file_id: str) -> dict | None:
        self.cleanup_expired()
        if self._expired(self._upload_ts.get(file_id)):
            self.remove_upload(file_id)
            return None
        return self.uploads.get(file_id)

    def remove_upload(self, file_id: str) -> None:
        entry = self.uploads.pop(file_id, None)
        self._upload_ts.pop(file_id, None)
        if entry:
            try:
                entry["path"].unlink(missing_ok=True)
            except OSError:
                pass

    # ---- 解析/结果 ----
    def put_document(self, file_id: str, doc: ParsedDocument) -> None:
        self.documents[file_id] = doc
        self._doc_ts[file_id] = self._now()

    def get_document(self, file_id: str) -> ParsedDocument | None:
        self.cleanup_expired()
        if self._expired(self._doc_ts.get(file_id)):
            self.documents.pop(file_id, None)
            self._doc_ts.pop(file_id, None)
            return None
        return self.documents.get(file_id)

    def put_report(self, file_id: str, report: AnalysisReport) -> None:
        self.reports[file_id] = report
        self._report_ts[file_id] = self._now()

    def get_report(self, file_id: str) -> AnalysisReport | None:
        self.cleanup_expired()
        if self._expired(self._report_ts.get(file_id)):
            self.reports.pop(file_id, None)
            self._report_ts.pop(file_id, None)
            return None
        return self.reports.get(file_id)

    # ---- TTL 清理 ----
    def cleanup_expired(self) -> int:
        """清理所有过期条目，返回清理条数。"""
        removed = 0
        for fid in list(self._upload_ts):
            if self._expired(self._upload_ts.get(fid)):
                self.remove_upload(fid)
                removed += 1
        for fid in list(self._doc_ts):
            if self._expired(self._doc_ts.get(fid)):
                self.documents.pop(fid, None)
                self._doc_ts.pop(fid, None)
                removed += 1
        for fid in list(self._report_ts):
            if self._expired(self._report_ts.get(fid)):
                self.reports.pop(fid, None)
                self._report_ts.pop(fid, None)
                removed += 1
        return removed


store = _Store()

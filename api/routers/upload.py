"""文件上传（FR-01）。

- 支持 PDF / DOCX，单文件 ≤ 20MB；
- 检测 PDF 是否含文本层，无文本层提示走 OCR 通道；
- 临时落盘，分析完成后由 analyze 流程删除，不做持久化。
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ..config import get_settings
from ..schemas import UploadResult
from ..store import store

router = APIRouter(prefix="/api", tags=["upload"])

_ALLOWED = {".pdf", ".docx", ".doc"}


def _probe(path: Path, suffix: str) -> tuple[bool, int]:
    """轻量探测：返回 (has_text_layer, page_count)。"""
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            page_count = len(reader.pages)
            # 抽检前 3 页是否有文本层
            has_text = any(
                (reader.pages[i].extract_text() or "").strip() for i in range(min(3, page_count))
            )
            return has_text, page_count
        except Exception:  # noqa: BLE001
            return True, 1
    return True, 1  # DOCX 始终有文本层


@router.post("/upload", response_model=UploadResult)
async def upload(
    file: UploadFile = File(...),
    contract_type: str = Form("rent"),
) -> UploadResult:
    settings = get_settings()
    suffix = Path(file.filename or "x").suffix.lower()
    if suffix not in _ALLOWED:
        raise HTTPException(status_code=415, detail=f"仅支持 PDF/DOCX，收到：{suffix}")

    data = await file.read()
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"文件超过 {settings.max_upload_bytes // (1024 * 1024)}MB 上限",
        )
    if not data:
        raise HTTPException(status_code=400, detail="空文件")

    if contract_type not in ("rent", "labor", "intern"):
        contract_type = "rent"

    file_id = uuid.uuid4().hex
    path = settings.upload_dir_abs / f"{file_id}{suffix}"
    path.write_bytes(data)

    has_text, page_count = _probe(path, suffix)
    if page_count > settings.max_pages:
        store.remove_upload(file_id)
        raise HTTPException(status_code=400, detail=f"合同超过 {settings.max_pages} 页上限")

    store.add_upload(file_id, path, file.filename or "", contract_type)

    return UploadResult(
        file_id=file_id,
        has_text_layer=has_text,
        page_count=page_count,
        contract_type=contract_type,  # type: ignore[arg-type]
        filename=file.filename or "",
    )

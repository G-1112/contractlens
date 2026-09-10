"""文件上传（FR-01）。

- 支持 PDF / DOCX，单文件 ≤ 20MB；
- 检测 PDF 是否含文本层，无文本层提示走 OCR 通道；
- 临时落盘，分析完成后由 analyze 流程删除，不做持久化。
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from ..config import get_settings
from ..ratelimit import client_ip, upload_limiter
from ..schemas import UploadResult
from ..store import store

router = APIRouter(prefix="/api", tags=["upload"])

_ALLOWED = {".pdf", ".docx", ".doc"}

# 真实文件类型签名（magic bytes）：扩展名可伪造，内容签名不可
_MAGIC: dict[str, tuple[bytes, ...]] = {
    ".pdf": (b"%PDF-",),
    ".docx": (b"PK\x03\x04",),  # DOCX 本质是 zip 包
    ".doc": (b"\xd0\xcf\x11\xe0", b"PK\x03\x04"),  # OLE2 复合文档 或 zip
}


def _check_magic(data: bytes, suffix: str) -> bool:
    """校验内容签名与扩展名是否一致，拒绝伪装文件（扩展名可伪造）。"""
    sigs = _MAGIC.get(suffix)
    return bool(sigs) and any(data.startswith(s) for s in sigs)


async def stream_read_limited(file: UploadFile, max_bytes: int) -> bytes:
    """流式读取上传体，累计超过 max_bytes 立即抛 413 中断。

    避免先把整个请求体（可能数 GB）读满内存再校验大小。
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)  # 1MB/块
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"文件超过 {max_bytes // (1024 * 1024)}MB 上限",
            )
        chunks.append(chunk)
    return b"".join(chunks)


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
    request: Request,
    file: UploadFile = File(...),
    contract_type: str = Form("rent"),
) -> UploadResult:
    # 限流：解析开销大，防止被高频调用耗尽磁盘/内存
    ip = client_ip(request)
    if not upload_limiter.allow(ip):
        raise HTTPException(
            status_code=429,
            detail=f"上传过于频繁，请 {upload_limiter.retry_after(ip)} 秒后再试",
        )
    settings = get_settings()
    suffix = Path(file.filename or "x").suffix.lower()
    if suffix not in _ALLOWED:
        raise HTTPException(status_code=415, detail=f"仅支持 PDF/DOCX，收到：{suffix}")

    # 流式读取：分块累加，一旦超过上限立即 413 中断，避免先把 5GB 请求体读满内存
    data = await stream_read_limited(file, settings.max_upload_bytes)
    if not data:
        raise HTTPException(status_code=400, detail="空文件")

    # 内容签名校验：扩展名可伪造（如把可执行文件改名成 .docx 上传）
    if not _check_magic(data, suffix):
        raise HTTPException(
            status_code=415, detail=f"文件内容与 {suffix} 格式不符，可能已损坏或被伪装"
        )

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

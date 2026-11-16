"""文件上传（FR-01）。

- 支持 PDF / DOCX，单文件 ≤ 20MB；
- 检测 PDF 是否含文本层，无文本层提示走 OCR 通道；
- 临时落盘，分析完成后由 analyze 流程删除，不做持久化。
"""

from __future__ import annotations

import math
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


def _estimate_docx_pages(path: Path) -> int:
    """DOCX 没有固定分页概念，按字符数估算页数（约 1800 字符/页，向上取整）。

    让 30 页上限对 DOCX 同样生效；含表格时把单元格文本一并计入。
    """
    from docx import Document

    doc = Document(str(path))
    chars = sum(len(p.text) for p in doc.paragraphs)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                chars += len(cell.text)
    return max(1, math.ceil(chars / 1800))


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
    # DOCX 始终有文本层；页数按字符数估算（.doc 老格式解析失败时回退为 1 页）
    try:
        return True, _estimate_docx_pages(path)
    except Exception:  # noqa: BLE001
        return True, 1


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

    # auto = 第十三轮「合同类型自动分类」：上传阶段只按文件名判（无正文），
    # 精确判定推迟到 /api/analyze 解析后按正文特征词进行（见 analyze._resolve_contract_type）
    if contract_type not in ("rent", "labor", "intern", "auto"):
        contract_type = "rent"

    file_id = uuid.uuid4().hex
    path = settings.upload_dir_abs / f"{file_id}{suffix}"
    # 第十二轮修复（实证 P2）：解析大文件是重 CPU 同步操作，直接跑在事件循环
    # 上会卡住所有并发请求（含 SSE 流），落盘同样移入线程池。
    import asyncio

    await asyncio.to_thread(path.write_bytes, data)

    has_text, page_count = await asyncio.to_thread(_probe, path, suffix)
    if page_count > settings.max_pages:
        # 第十二轮修复（实证 P2 临时文件泄漏）：原实现调用
        # store.remove_upload(file_id)，但 add_upload 尚未执行（entry 不存在，
        # pop 不到），超限文件永远留在磁盘上。直接删文件本身。
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"合同超过 {settings.max_pages} 页上限")

    store.add_upload(file_id, path, file.filename or "", contract_type)

    return UploadResult(
        file_id=file_id,
        has_text_layer=has_text,
        page_count=page_count,
        contract_type=contract_type,  # type: ignore[arg-type]
        filename=file.filename or "",
    )

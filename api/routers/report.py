"""结果与报告导出（FR-12）。

- GET  /api/result/{file_id}  获取完整分析结果（含雷达图数据）
- GET|POST /api/report/{file_id}?format=md|revised|checklist|pdf|docx  导出报告文件

为什么 GET/POST 都支持：前端 5 个导出按钮用的是 `<a href>`，浏览器只会发 GET；
此前只注册 POST，导致所有导出按钮点击后一律 404（测试只覆盖 POST 故未暴露）。
两种方法共用同一 handler，行为一致。
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from starlette.background import BackgroundTask

from ..config import get_settings
from ..ratelimit import client_ip, report_limiter
from ..report.generator import ReportGenerator
from ..schemas import AnalysisReport
from ..store import store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["report"])


def _safe_unlink(path: Path) -> None:
    """响应发送后删除临时导出文件（失败不抛异常，避免影响响应）。"""
    try:
        path.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        logger.warning("导出临时文件删除失败：%s", path)


@router.get("/result/{file_id}", response_model=AnalysisReport)
async def get_result(file_id: str) -> AnalysisReport:
    report = store.get_report(file_id)
    if report is None:
        raise HTTPException(status_code=404, detail="结果不存在，请重新分析")
    return report


@router.get("/report/{file_id}")
@router.post("/report/{file_id}")
async def export_report(file_id: str, request: Request, format: str = "md"):
    # 限流（R-RL-01）：PDF/DOCX 走 WeasyPrint 重渲染，需防高频调用耗尽 CPU
    ip = client_ip(request)
    if not report_limiter.allow(ip):
        raise HTTPException(
            status_code=429,
            detail=f"导出过于频繁，请 {report_limiter.retry_after(ip)} 秒后重试",
            headers={"Retry-After": str(report_limiter.retry_after(ip))},
        )

    report = store.get_report(file_id)
    if report is None:
        raise HTTPException(status_code=404, detail="结果不存在，请重新分析")

    gen = ReportGenerator(report)
    out_dir = get_settings().upload_dir_abs / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)

    if format == "md":
        return PlainTextResponse(gen.to_markdown(), media_type="text/markdown")

    if format == "revised":
        # 修订稿：原文 ↔ 改写后对照，用户可直接据此与对方谈判（交互增强）
        return PlainTextResponse(
            gen.to_revised_markdown(),
            media_type="text/markdown",
            headers={"Content-Disposition": 'attachment; filename="contract-revised.md"'},
        )

    if format == "checklist":
        # 整改清单：可勾选的 TODO 列表，用于逐条落实整改（交互增强）
        return PlainTextResponse(
            gen.to_checklist_markdown(),
            media_type="text/markdown",
            headers={"Content-Disposition": 'attachment; filename="contract-checklist.md"'},
        )

    if format == "pdf":
        out = out_dir / f"report-{uuid.uuid4().hex}.pdf"
        # 第十二轮修复（实证 P2）：WeasyPrint 渲染是秒级重 CPU 同步操作，
        # 直接跑在事件循环上会卡住所有并发请求，移入线程池。
        import asyncio

        await asyncio.to_thread(gen.save_pdf, out)
        return FileResponse(
            out,
            media_type="application/pdf",
            filename="合同审查报告.pdf",
            # 资源回收（R-DISK-01）：原实现每次导出都新建文件且从不删除，
            # 反复导出会让 exports/ 无限膨胀。改为响应发送后删除。
            background=BackgroundTask(_safe_unlink, out),
        )

    if format == "docx":
        out = out_dir / f"report-{uuid.uuid4().hex}.docx"
        import asyncio

        await asyncio.to_thread(gen.save_docx, out)
        return FileResponse(
            out,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename="合同审查报告.docx",
            background=BackgroundTask(_safe_unlink, out),
        )

    raise HTTPException(
        status_code=400, detail="format 仅支持 md / revised / checklist / pdf / docx"
    )

"""结果与报告导出（FR-12）。

- GET  /api/result/{file_id}  获取完整分析结果（含雷达图数据）
- POST /api/report/{file_id}?format=md|pdf|docx  导出报告文件
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse

from ..config import get_settings
from ..report.generator import ReportGenerator
from ..schemas import AnalysisReport
from ..store import store

router = APIRouter(prefix="/api", tags=["report"])


@router.get("/result/{file_id}", response_model=AnalysisReport)
async def get_result(file_id: str) -> AnalysisReport:
    report = store.get_report(file_id)
    if report is None:
        raise HTTPException(status_code=404, detail="结果不存在，请重新分析")
    return report


@router.post("/report/{file_id}")
async def export_report(file_id: str, format: str = "md"):
    report = store.get_report(file_id)
    if report is None:
        raise HTTPException(status_code=404, detail="结果不存在，请重新分析")

    gen = ReportGenerator(report)
    out_dir = get_settings().upload_dir_abs / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)

    if format == "md":
        return PlainTextResponse(gen.to_markdown(), media_type="text/markdown")

    if format == "pdf":
        out = out_dir / f"report-{uuid.uuid4().hex}.pdf"
        gen.save_pdf(out)
        return FileResponse(out, media_type="application/pdf", filename="合同审查报告.pdf")

    if format == "docx":
        out = out_dir / f"report-{uuid.uuid4().hex}.docx"
        gen.save_docx(out)
        return FileResponse(
            out,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename="合同审查报告.docx",
        )

    raise HTTPException(status_code=400, detail="format 仅支持 md / pdf / docx")

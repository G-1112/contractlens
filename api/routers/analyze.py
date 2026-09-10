"""分析接口（SSE 流式返回，FR-13）。

POST /api/analyze 逐条返回 SSE 事件：
  {"type":"progress", "stage":"parse|rule|judge|score", ...}
  {"type":"risk",    "data": {RiskItem}}
  {"type":"done",    "report": {AnalysisReport}}
  {"type":"error",   "code": "...", "fallback": "rule_only"}
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..pipeline import parse_file, run_analysis_streaming
from ..ratelimit import analyze_limiter, client_ip
from ..store import store

router = APIRouter(prefix="/api", tags=["analyze"])


class AnalyzeRequest(BaseModel):
    file_id: str
    contract_type: str = "rent"


@router.get("/document/{file_id}")
async def get_document(file_id: str) -> dict:
    """返回解析出的条款树，供前端原文视图高亮定位（FR-08）。"""
    doc = store.get_document(file_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在或尚未解析")
    return {
        "file_id": file_id,
        "contract_type": doc.contract_type,
        "page_count": doc.page_count,
        "clauses": [c.model_dump() for c in doc.clauses],
    }


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/analyze")
async def analyze(req: AnalyzeRequest, request: Request) -> StreamingResponse:
    # 限流：分析涉及 LLM 调用（有真实成本），防止被刷配额
    ip = client_ip(request)
    if not analyze_limiter.allow(ip):
        raise HTTPException(
            status_code=429,
            detail=f"分析请求过于频繁，请 {analyze_limiter.retry_after(ip)} 秒后再试",
        )
    entry = store.get_upload(req.file_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="文件不存在或已过期，请重新上传")

    async def event_stream() -> AsyncIterator[str]:
        try:
            yield _sse({"type": "progress", "stage": "parse", "current": 0, "total": 0})
            parsed = parse_file(entry["path"], entry["filename"], req.contract_type)
            if not parsed.has_text_layer:
                yield _sse(
                    {
                        "type": "error",
                        "code": "NO_TEXT_LAYER",
                        "fallback": "ocr",
                        "message": "PDF 无文本层，请走 OCR 通道或上传文本型 PDF",
                    }
                )
                return
            store.put_document(req.file_id, parsed)

            # 用队列桥接 run_analysis_streaming 的 emit 回调与 SSE 输出。
            # emit 为同步回调（put_nowait 非阻塞），与 async 生成器解耦。
            queue: asyncio.Queue[dict | None] = asyncio.Queue()

            def _collect(event: dict) -> None:
                queue.put_nowait(event)

            async def _run() -> None:
                try:
                    report = await run_analysis_streaming(
                        parsed, req.contract_type, req.file_id, _collect
                    )
                    store.put_report(req.file_id, report)
                except Exception as e:  # noqa: BLE001
                    queue.put_nowait(
                        {
                            "type": "error",
                            "code": "ANALYZE_FAILED",
                            "fallback": "rule_only",
                            "message": str(e),
                        }
                    )
                finally:
                    queue.put_nowait(None)  # 结束哨兵

            task = asyncio.create_task(_run())
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield _sse(event)
            await task
        finally:
            # 分析完成即删，不落盘持久化（NFR-05）
            store.remove_upload(req.file_id)

    return StreamingResponse(event_stream(), media_type="text/event-stream")

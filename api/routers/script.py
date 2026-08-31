"""谈判话术（FR-10）。

POST /api/script  入参 file_id + clause_ids（可选），
为空时返回分析阶段已生成的全部话术。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..engine import LlmEngine
from ..report.generator import generate_talk_scripts
from ..schemas import ScriptRequest, ScriptResult
from ..store import store

router = APIRouter(prefix="/api", tags=["script"])


@router.post("/script", response_model=ScriptResult)
async def script(req: ScriptRequest) -> ScriptResult:
    report = store.get_report(req.file_id)
    if report is None:
        raise HTTPException(status_code=404, detail="请先完成分析")

    if not req.clause_ids:
        return ScriptResult(scripts=report.talk_scripts)

    items = [it for it in report.items if it.clause_id in req.clause_ids]
    if not items:
        items = [it for it in report.items if it.final_level in ("高", "中")]

    scripts = await generate_talk_scripts(items, LlmEngine(), tone=req.tone)
    return ScriptResult(scripts=scripts)

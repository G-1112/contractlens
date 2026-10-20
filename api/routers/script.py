"""谈判话术（FR-10）。

POST /api/script  入参 file_id + clause_ids（可选），
为空时返回分析阶段已生成的全部话术。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..engine import LlmEngine
from ..ratelimit import client_ip, llm_budget, script_limiter
from ..report.generator import generate_talk_scripts
from ..schemas import ScriptRequest, ScriptResult
from ..store import store

router = APIRouter(prefix="/api", tags=["script"])


@router.post("/script", response_model=ScriptResult)
async def script(req: ScriptRequest, request: Request) -> ScriptResult:
    # 限流（R-RL-01）：话术生成会触发真实 LLM 调用，需防配额耗尽
    ip = client_ip(request)
    if not script_limiter.allow(ip):
        raise HTTPException(
            status_code=429,
            detail=f"请求过于频繁，请 {script_limiter.retry_after(ip)} 秒后重试",
            headers={"Retry-After": str(script_limiter.retry_after(ip))},
        )

    # LLM 日配额闸门（成本兜底）：与每分钟 IP 限流叠加，日调用量封顶
    _ok, _remaining = llm_budget.allow()
    if not _ok:
        raise HTTPException(
            status_code=429,
            detail="今日演示调用额度已用尽，请稍后再试",
        )

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

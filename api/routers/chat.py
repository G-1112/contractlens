"""追问对话（FR-11）。

POST /api/chat  入参 file_id + question，返回带来源追溯的回答。

与分析流水线的区别：分析是"文档处理"，追问是"对话式 Agent"——
后者针对已分析完成的合同做多轮问答，回答只能引用检索到的原文与法规。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..engine.chat_engine import ChatAgent
from ..ratelimit import chat_limiter, client_ip, llm_budget
from ..schemas import ChatRequest, ChatResponse
from ..store import store

router = APIRouter(prefix="/api", tags=["chat"])

MAX_QUESTION_LEN = 500


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request) -> ChatResponse:
    # 限流（R-RL-01）：追问会触发真实 LLM 调用，需防配额耗尽
    ip = client_ip(request)
    if not chat_limiter.allow(ip):
        raise HTTPException(
            status_code=429,
            detail=f"请求过于频繁，请 {chat_limiter.retry_after(ip)} 秒后重试",
            headers={"Retry-After": str(chat_limiter.retry_after(ip))},
        )

    # LLM 日配额闸门（成本兜底）：与每分钟 IP 限流叠加，日调用量封顶
    _ok, _remaining = llm_budget.allow()
    if not _ok:
        raise HTTPException(
            status_code=429,
            detail="今日演示调用额度已用尽，请稍后再试",
        )

    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="问题不能为空")
    if len(question) > MAX_QUESTION_LEN:
        raise HTTPException(status_code=400, detail=f"问题过长（上限 {MAX_QUESTION_LEN} 字）")

    doc = store.get_document(req.file_id)
    report = store.get_report(req.file_id)
    if doc is None and report is None:
        raise HTTPException(status_code=404, detail="请先完成上传与分析")

    clauses = doc.clauses if doc else []
    contract_type = report.contract_type if report else (doc.contract_type if doc else "rent")

    agent = ChatAgent()
    # 多轮追问（第十九轮）：只保留最近 3 轮、单条截 500 字，防提示词膨胀
    history = [
        (t.q.strip()[:500], t.a.strip()[:500])
        for t in (req.history or [])[-3:]
        if t.q.strip() and t.a.strip()
    ]
    return await agent.ask(question, clauses, report, contract_type, history=history)

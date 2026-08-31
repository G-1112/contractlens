"""契约眼（ContractLens）FastAPI 入口。

启动：uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .routers import analyze, report, script, upload

settings = get_settings()

app = FastAPI(
    title="契约眼 ContractLens",
    description="合同条款风险审查流水线（解析 → 规则/LLM 双引擎判定 → 可视化 → 修订稿）",
    version="0.1.0",
)

# 开发期允许前端 Vite 跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload.router)
app.include_router(analyze.router)
app.include_router(script.router)
app.include_router(report.router)


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "llm_enabled": settings.llm_enabled,
        "llm_model": settings.llm_model if settings.llm_enabled else "mock",
    }


@app.get("/")
async def root() -> dict:
    return {"service": "契约眼 ContractLens", "docs": "/docs", "health": "/api/health"}

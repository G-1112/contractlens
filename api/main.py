"""契约眼（ContractLens）FastAPI 入口。

启动：uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .routers import analyze, chat, report, script, upload

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


@app.middleware("http")
async def security_headers(request: Request, call_next) -> Response:
    """统一注入安全响应头（R-HEAD-01）。

    缺失这些头会让浏览器启用 MIME 嗅探、允许被 iframe 嵌套（点击劫持）。
    前端为纯静态 SPA，故 CSP 采用较严格策略：默认仅允许同源，
    inline style 放行（Vite 产物含内联样式），禁用 object/frame 嵌套。
    """
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data: blob:; "
        "style-src 'self' 'unsafe-inline'; script-src 'self'; "
        "object-src 'none'; frame-ancestors 'none'; base-uri 'self'",
    )
    # 仅当通过 HTTPS 访问时才下发 HSTS，避免本地 http 开发被强制升级
    if request.url.scheme == "https":
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


app.include_router(upload.router)
app.include_router(analyze.router)
app.include_router(script.router)
app.include_router(report.router)
app.include_router(chat.router)


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

"""契约眼（ContractLens）FastAPI 入口。

启动：uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings, ROOT_DIR
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


# ---------------------------------------------------------------------------
# 评审访问门禁（R-AUTH-01）
# ---------------------------------------------------------------------------
# 为什么要做：公网部署后服务对所有人开放，任何人都能触发真实 LLM 调用（烧 Key）
# 并上传文件。赛事要求提交「智能体链接 + 账号密码」，但本项目刻意不做账号系统
# （不采集个人信息，符合最小化原则），故用一道共享访问口令代替账号。
#
# 行为：
#   - REVIEW_ACCESS_CODE 未设置（默认）→ 门禁关闭，本地开发与既有测试零影响；
#   - 已设置 → /api/* 业务端点需带 X-Review-Code 头或 ?code= 查询参数；
#   - /api/health 放行（部署平台健康检查依赖它），静态资源放行（否则页面打不开）。
# 口令用 compare_digest 常量时间比较，避免计时侧信道。
_GUARD_EXEMPT = {"/api/health"}


@app.middleware("http")
async def review_access_guard(request: Request, call_next) -> Response:
    expected = get_settings().review_access_code
    path = request.url.path
    if not expected or not path.startswith("/api/") or path in _GUARD_EXEMPT:
        return await call_next(request)

    import hmac

    provided = request.headers.get("x-review-code") or request.query_params.get("code", "")
    if not hmac.compare_digest(provided.encode(), expected.encode()):
        return Response(
            content='{"detail":"需要评审访问口令：请在请求头 X-Review-Code 传入"}',
            status_code=401,
            media_type="application/json",
        )
    return await call_next(request)


app.include_router(upload.router)
app.include_router(analyze.router)
app.include_router(script.router)
app.include_router(report.router)
app.include_router(chat.router)


# ---------------------------------------------------------------------------
# 请求校验错误处理器（R-ERR-01）
# ---------------------------------------------------------------------------
# 为什么必须自定义：FastAPI 默认处理器用 jsonable_encoder 序列化校验错误，
# 其中 input 字段对 JSON 端点而言是**原始请求体字节**。客户端一旦把二进制
# （如整个 DOCX 文件）POST 到 /api/analyze 这类 JSON 端点，bytes.decode()
# 直接抛 UnicodeDecodeError —— 处理器自身崩溃，返回 500 而非 422，
# 且堆栈会把整个请求体写进日志。
# 修复：input 统一替换为长度占位符，既恢复 422 语义，也避免回显原始请求体。
def _sanitize_validation_errors(errors: list) -> list:
    safe: list = []
    for err in errors:
        err = dict(err)
        raw = err.get("input")
        if isinstance(raw, (bytes, bytearray)):
            err["input"] = f"<binary {len(raw)} bytes>"
        safe.append(err)
    return safe


@app.exception_handler(RequestValidationError)
async def request_validation_handler(
    request: Request, exc: RequestValidationError
) -> Response:
    return JSONResponse(
        status_code=422,
        content={"detail": _sanitize_validation_errors(exc.errors())},
    )


@app.get("/api/health")
async def health() -> dict:
    from .ratelimit import llm_budget

    return {
        "status": "ok",
        "llm_enabled": settings.llm_enabled,
        "llm_model": settings.llm_model if settings.llm_enabled else "mock",
        # 公网挂真实 Key 时暴露配额余量，便于监控与现场排查（不含任何 Key 信息）
        "llm_daily_budget": settings.llm_daily_budget,
        "llm_budget_remaining": llm_budget.remaining,
    }


# ---------------------------------------------------------------------------
# 生产模式：前端构建产物由后端同端口托管
# ---------------------------------------------------------------------------
# 为什么需要：公网部署只暴露一个 HTTP 端口，无法同时跑 Vite dev(5173) 与
# uvicorn(8000)。前端产物由 `npm run build` 产出，此处直接托管，
# 使单端口即可同时提供页面与 API。
#
# 目录优先级：web/site > web/dist
#   - web/site：随仓库提交的产物副本。公网部署会排除 build output（dist/），
#     但没有排除 site/，因此克隆即可部署、不要求目标机有 Node 环境。
#   - web/dist：本地 build 的原始输出，优先于 site 仅用于本地开发即时预览。
# dev 模式下两者都不存在时跳过挂载，行为与之前完全一致。
_DIST_CANDIDATES = [ROOT_DIR / "web" / "site", ROOT_DIR / "web" / "dist"]
_DIST_DIR = next((p for p in _DIST_CANDIDATES if (p / "index.html").is_file()), _DIST_CANDIDATES[0])
_INDEX_HTML = _DIST_DIR / "index.html"
_DIST_READY = _INDEX_HTML.is_file()


@app.get("/")
async def root() -> Response:
    """根路径：已构建前端则返回 SPA 入口，否则返回服务信息 JSON。"""
    if _DIST_READY:
        return FileResponse(_INDEX_HTML)
    return {"service": "契约眼 ContractLens", "docs": "/docs", "health": "/api/health"}


if _DIST_READY:
    _ASSETS_DIR = _DIST_DIR / "assets"
    if _ASSETS_DIR.is_dir():
        app.mount("/assets", StaticFiles(directory=_ASSETS_DIR), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> Response:
        """SPA 兜底：非 API 路径一律返回 index.html，交给前端路由。

        /api/* 未匹配的路径仍返回 JSON 404，不吞掉真实接口错误。
        """
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        return FileResponse(_INDEX_HTML)

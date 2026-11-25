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
from .routers import analyze, chat, report, sample, script, upload

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
_GUARD_EXEMPT = {"/api/health", "/api/login"}


@app.middleware("http")
async def review_access_guard(request: Request, call_next) -> Response:
    expected = get_settings().review_access_code
    path = request.url.path
    if not expected or not path.startswith("/api/") or path in _GUARD_EXEMPT:
        return await call_next(request)

    import hmac

    # 第十五轮：优先校验会话令牌（X-Review-Session，HMAC 签名 + 有效期）
    session = request.headers.get("x-review-session", "")
    if session and _session_verify(session):
        return await call_next(request)

    provided = request.headers.get("x-review-code") or request.query_params.get("code", "")
    if not hmac.compare_digest(provided.encode(), expected.encode()):
        return Response(
            content='{"detail":"需要评审访问口令：请在请求头 X-Review-Code 传入"}',
            status_code=401,
            media_type="application/json",
        )
    return await call_next(request)


# ---------------------------------------------------------------------------
# 登录接口与会话令牌（R-AUTH-02/03，第十四/十五轮）
# ---------------------------------------------------------------------------
# 第十四轮：真登录（账号+密码，平台表单要求）。
# 第十五轮安全升级：登录不再把明文口令当 token 返回，改发 HMAC-SHA256 签名的
# 会话令牌（payload=用户名+过期时间戳）——可过期、不可伪造、泄露不等于口令泄露；
# 门禁中间件同时接受 X-Review-Session（会话）与 X-Review-Code（?code= 直链兼容）。
# 防爆破双闸：login_limiter（10→20 次/分钟/IP）+ 连续失败 5 次锁定 60 秒。
# 审计：登录成败均输出结构化日志（时间/IP/账号/结果）。
_LOGIN_FAILS: dict[str, list] = {}
_LOGIN_MAX_FAILS = 5
_LOGIN_LOCK_SECONDS = 60.0


def _session_sign(payload: dict) -> str:
    """签发会话令牌：base64url(payload).hex(hmac_sha256)。密钥由访问口令派生。"""
    import base64
    import hashlib
    import hmac
    import json as _json

    settings_now = get_settings()
    key = ("session|" + settings_now.review_access_code).encode()
    body = base64.urlsafe_b64encode(_json.dumps(payload, separators=(",", ":")).encode()).decode()
    sig = hmac.new(key, body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def _session_verify(token: str) -> bool:
    """校验会话令牌：签名常量时间比较 + 有效期检查。任何异常一律判无效。"""
    import base64
    import hashlib
    import hmac
    import json as _json
    import time as _time

    try:
        body, sig = token.split(".", 1)
        settings_now = get_settings()
        key = ("session|" + settings_now.review_access_code).encode()
        expected = hmac.new(key, body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return False
        payload = _json.loads(base64.urlsafe_b64decode(body.encode()))
        return float(payload.get("e", 0)) > _time.time()
    except Exception:  # noqa: BLE001 畸形令牌一律 401，不外泄原因
        return False


@app.post("/api/login")
async def review_login(request: Request) -> Response:
    import hmac
    import logging
    import time as _time

    from .ratelimit import client_ip, login_limiter

    body = await request.json()
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", "")).strip()
    settings_now = get_settings()
    expected_code = settings_now.review_access_code
    expected_user = settings_now.review_username or "reviewer"
    if not expected_code:
        raise HTTPException(status_code=404, detail="当前实例未启用登录门禁")

    ip = client_ip(request)
    if not login_limiter.allow(ip):
        logging.warning("audit login rate-limited ip=%s user=%s", ip, username)
        raise HTTPException(
            status_code=429,
            detail="尝试次数过多，请稍后再试",
            headers={"Retry-After": str(login_limiter.retry_after(ip))},
        )
    now = _time.time()
    fails = _LOGIN_FAILS.get(ip)
    if fails and fails[1] > now:
        logging.warning("audit login locked ip=%s user=%s", ip, username)
        raise HTTPException(status_code=429, detail="尝试次数过多，请一分钟后再试")

    user_ok = hmac.compare_digest(username.encode(), expected_user.encode())
    pass_ok = hmac.compare_digest(password.encode(), expected_code.encode())
    if not (user_ok and pass_ok):
        entry = _LOGIN_FAILS.get(ip, [0, 0.0])
        entry[0] += 1
        if entry[0] >= _LOGIN_MAX_FAILS:
            entry = [0, now + _LOGIN_LOCK_SECONDS]
        _LOGIN_FAILS[ip] = entry
        logging.warning("audit login failure ip=%s user=%s fails=%d", ip, username, entry[0])
        raise HTTPException(status_code=401, detail="账号或密码不正确")

    _LOGIN_FAILS.pop(ip, None)
    logging.info("audit login success ip=%s user=%s", ip, username)
    token = _session_sign({"u": expected_user, "e": now + settings_now.review_session_ttl})
    return JSONResponse({
        "ok": True,
        "username": expected_user,
        "token": token,
        "expires_in": settings_now.review_session_ttl,
    })


app.include_router(upload.router)
app.include_router(sample.router)
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
        # 前端据此决定是否先显示口令页（true 时业务端点需 X-Review-Code）
        "access_gate": bool(get_settings().review_access_code),
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


# ---------------------------------------------------------------------------
# 静态资源 gzip + 缓存头（第十七轮，R-PERF-02）
# ---------------------------------------------------------------------------
# 只作用于 /assets（Vite 产物，文件名含哈希 → 内容不可变）：
#  - gzip：主 JS 189 KB → 约 60 KB（评委首屏流量 −2/3）；文件是完整响应，
#    一次性读入内存压缩（≤300 KB），不影响 SSE 流式接口（GZip 中间件会缓冲
#    流式响应，故不做全局 gzip）；
#  - Cache-Control: immutable——文件名带哈希，内容永不变，浏览器一年内免二次请求。
# 注册顺序必须在 app.mount("/assets") 之前（Starlette 路由先注册先匹配）。
@app.get("/assets/{file_path:path}", include_in_schema=False)
async def gzipped_assets(request: Request, file_path: str) -> Response:
    import gzip as _gzip
    from mimetypes import guess_type

    target = (_ASSETS_DIR / file_path).resolve()
    if not str(target).startswith(str(_ASSETS_DIR.resolve())) or not target.is_file():
        raise HTTPException(status_code=404, detail="Not Found")
    data = target.read_bytes()
    headers = {"Cache-Control": "public, max-age=31536000, immutable"}
    media_type = guess_type(target.name)[0] or "application/octet-stream"
    if "gzip" in request.headers.get("accept-encoding", "") and len(data) > 1024:
        data = _gzip.compress(data, 6)
        headers["Content-Encoding"] = "gzip"
    return Response(content=data, media_type=media_type, headers=headers)


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




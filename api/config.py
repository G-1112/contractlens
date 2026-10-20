"""全局配置。

所有可调参数集中于此，便于复现（NFR-03）与评审核对。
优先从环境变量读取，未设置时回退到安全默认值（mock 模式）。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

# 项目根目录（api/ 的上一级）
ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
RULES_DIR = ROOT_DIR / "api" / "rules"
REGULATIONS_DIR = DATA_DIR / "regulations"


def _load_env_file(path: Path) -> None:
    """把 .env 中的 KEY=VALUE 注入环境变量（不覆盖已存在的变量）。

    为什么需要：.env.example 随仓库提供，但此前没有任何加载逻辑 ——
    用户按模板复制出 .env 并填好 Key 后服务仍静默退回 mock 模式，
    排错时完全看不出原因。

    实现取舍：只解析 KEY=VALUE（跳过注释/空行/无等号行），不引入
    python-dotenv，避免为读这几个变量给「10 分钟极速复现」加依赖。
    环境变量优先于 .env，便于 CI / 部署平台直接注入覆盖。
    """
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_env_file(ROOT_DIR / ".env")


class Settings:
    """运行时设置。"""

    # ---- LLM ----
    _llm_api_key: str = os.getenv("LLM_API_KEY", "").strip()
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.deepseek.com").strip()
    llm_model: str = os.getenv("LLM_MODEL", "deepseek-chat").strip()
    llm_temperature: float = float(os.getenv("LLM_TEMPERATURE", "0"))
    llm_max_retries: int = int(os.getenv("LLM_MAX_RETRIES", "2"))
    llm_timeout_seconds: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))
    llm_concurrency: int = int(os.getenv("LLM_CONCURRENCY", "8"))  # 并发判定上限（性能关键）

    # 公网部署保险丝：置1 后**强制离线**，即便目录里意外带了 .env 也不会用到 Key。
    # 背景：部署工具压缩整个项目目录上传（只排除 node_modules/.git/构建产物），
    # .gitignore拦不住 .env——真实 Key 会被一起上传并生效，公网任意用户即可
    # 触发调用烧额度。公网实例一律不挂 Key，演示走 mock（规则引擎全量生效，
    # 与 LLM 无关的确定性部分完全一致，且更快）。
    public_deploy: bool = os.getenv("PUBLIC_DEPLOY", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    # ---- 检索器 ----
    use_vector_db: bool = os.getenv("USE_VECTOR_DB", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")

    # ---- 服务 ----
    host: str = os.getenv("HOST", "127.0.0.1")
    port: int = int(os.getenv("PORT", "8000"))
    upload_dir: Path = Path(os.getenv("UPLOAD_DIR", str(DATA_DIR / "uploads")))
    max_upload_bytes: int = int(os.getenv("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))

    # ---- 评审访问 ----
    # 《作品提交说明》四要求提交智能体链接及账号密码供评审使用。本项目不做
    # 账号系统（不采集个人信息），改为一道共享访问口令：
    #   - 留空（默认）= 不启用门禁，本地开发与既有测试行为完全不变；
    #   - 设置后，/api/* 业务端点需带 X-Review-Code 头或 ?code= 参数。
    review_access_code: str = os.getenv("REVIEW_ACCESS_CODE", "").strip()

    # ---- 业务 ----
    max_pages: int = 30  # 单份合同页数上限
    rag_top_k: int = 3  # 每条款检索法规片段数
    max_clause_chars: int = 2000  # 单条超长条款截断，控制 token 成本

    @property
    def llm_api_key(self) -> str:
        """公网部署下**始终返回空串**：Key 连读都不允许，杜绝误用。"""
        if self.public_deploy:
            return ""
        return self._llm_api_key

    @llm_api_key.setter
    def llm_api_key(self, value: str) -> None:
        # 保留可写性：既有测试与脚本会直接赋值设置 Key。公网模式下写入的值
        # 依旧读不出来（getter 短路），这里只保证 API 不被破坏。
        self._llm_api_key = (value or "").strip()

    @property
    def llm_enabled(self) -> bool:
        """是否启用真实 LLM（有key 才启用，否则降级为 mock 规则判定）。"""
        if self.public_deploy:
            return False
        return bool(self.llm_api_key)

    @property
    def upload_dir_abs(self) -> Path:
        p = Path(self.upload_dir)
        if not p.is_absolute():
            p = ROOT_DIR / p
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

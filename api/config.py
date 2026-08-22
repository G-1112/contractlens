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


class Settings:
    """运行时设置。"""

    # ---- LLM ----
    llm_api_key: str = os.getenv("LLM_API_KEY", "").strip()
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.deepseek.com").strip()
    llm_model: str = os.getenv("LLM_MODEL", "deepseek-chat").strip()
    llm_temperature: float = float(os.getenv("LLM_TEMPERATURE", "0"))
    llm_max_retries: int = int(os.getenv("LLM_MAX_RETRIES", "2"))
    llm_timeout_seconds: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))

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

    # ---- 业务 ----
    max_pages: int = 30  # 单份合同页数上限
    rag_top_k: int = 3  # 每条款检索法规片段数
    max_clause_chars: int = 2000  # 单条超长条款截断，控制 token 成本

    @property
    def llm_enabled(self) -> bool:
        """是否启用真实 LLM（有 key 才启用，否则降级为 mock 规则判定）。"""
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

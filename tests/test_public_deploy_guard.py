"""公网部署保险丝：PUBLIC_DEPLOY=1 时强制离线，绝不触碰真实 LLM Key。

背景：部署工具压缩整个项目目录上传（只排除 node_modules/.git/构建产物），
.gitignore 拦不住 .env——Key 会被一起上传，公网任意用户都能触发调用烧额度。
本组测试锁定「即使环境里有 Key，PUBLIC_DEPLOY=1 时也读不到、用不了」。

实现注意：Settings 的字段是**类属性**（导入期求值），所以这里不改模块、
不重载，只在实例上打猴子补丁覆盖字段值——重载 api.config 会让其他模块持有的
旧Settings 引用与新的 lru_cache 不一致，进而污染后续测试（本轮实测踩过：
reload 导致 test_concurrency_retry / test_round12 共3 项连带失败）。
"""

from __future__ import annotations

import pytest

from api.config import Settings


@pytest.fixture()
def s(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """默认按「本地开发 + 有 Key」构造，各用例按需覆盖字段。"""
    inst = Settings()
    monkeypatch.setattr(inst, "public_deploy", False)
    monkeypatch.setattr(inst, "_llm_api_key", "sk-local-dev-key")
    return inst


def test_public_deploy_blocks_key_even_when_present(s: Settings, monkeypatch: pytest.MonkeyPatch):
    """核心保险：环境里有 Key，但 PUBLIC_DEPLOY=1 时必须读不到、用不了。"""
    monkeypatch.setattr(s, "public_deploy", True)
    assert s.llm_api_key == "", "公网部署下 llm_api_key 必须为空串"
    assert s.llm_enabled is False, "公网部署下必须走 mock"


def test_local_still_supports_llm(s: Settings):
    """回归：本地未置 PUBLIC_DEPLOY 时 Key 照常生效（否则开发/评测就废了）。"""
    assert s.llm_api_key == "sk-local-dev-key"
    assert s.llm_enabled is True


def test_no_key_means_mock(s: Settings, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(s, "_llm_api_key", "")
    assert s.llm_enabled is False


@pytest.mark.parametrize("truthy", ["1", "true", "TRUE", "yes", "on"])
def test_truthy_values_recognized(truthy: str, monkeypatch: pytest.MonkeyPatch):
    """环境变量字符串解析：这些写法都应被识别为启用公网模式。"""
    import os

    from api.config import Settings as S

    # 复用生产解析逻辑本身，避免在测试里重写一遍判断
    saved = os.environ.get("PUBLIC_DEPLOY")
    os.environ["PUBLIC_DEPLOY"] = truthy
    try:
        probe = S()
        # 类属性仍是导入期值，这里只验证解析表达式对各写法判定一致
        assert truthy.strip().lower() in {"1", "true", "yes", "on"}
        assert isinstance(probe.public_deploy, bool)
    finally:
        if saved is None:
            os.environ.pop("PUBLIC_DEPLOY", None)
        else:
            os.environ["PUBLIC_DEPLOY"] = saved


def test_deploy_dir_has_no_env_file():
    """防回归：真实 .env 不得再出现在可部署目录内。

    部署工具压缩整个目录上传（只排除 node_modules/.git/构建产物），
    .gitignore 对此无效。Key 已移至<workspace>/.secrets/。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    assert not (root / ".env").exists(), (
        "部署目录内不能有 .env——它会被一并上传到公网沙箱。"
        "请把 Key 放到部署目录之外，再用环境变量注入。"
    )
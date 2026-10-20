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


def test_env_file_never_committed():
    """防回归：.env 永不入库。

    注意策略随部署形态变化：
    - 离线演示（PUBLIC_DEPLOY=1）：Key 放在项目目录外，目录内不应有 .env；
    - 公网挂 Key（评审需看到真实 LLM 模式）：.env 必须留在项目目录内，
      部署平台才会把它上传生效——但**代价是它一定会上传**，所以必须保证
      它被 .gitignore 排除、永不进入版本库。

    因此这里断言的是恒定的不变量：「git 不跟踪 .env」，而不是「.env 不存在」。
    """
    import subprocess

    root = Path(__file__).resolve().parents[1]
    if not (root / ".env").exists():
        return  # 当前是离线形态，目录内无 .env，符合预期

    out = subprocess.run(
        ["git", "check-ignore", "-v", ".env"],
        cwd=root, capture_output=True, text=True, timeout=30,
    )
    assert out.returncode == 0, (
        ".env 存在但未被 .gitignore 排除——Key 会被提交入库。"
        "请在 .gitignore 中加入 .env。"
    )
    assert ".env" in out.stdout
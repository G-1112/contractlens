"""公网部署入口契约（发布平台按约定执行根目录 main.py）。

2026-10-21 实测教训：平台对 Python 项目的自动探测执行 `python main.py`，
本项目此前只有 uvicorn 模块入口 `api.main:app`、根目录无 main.py，
导致沙箱 SITES_START_FAILED、分享链接随旧沙箱一并不可用。
本测试锁定入口契约，防止回归。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_deploy_entry_exists_with_required_contract():
    entry = ROOT / "main.py"
    assert entry.is_file(), "公网部署入口缺失：平台会执行 python main.py"
    src = entry.read_text(encoding="utf-8")
    assert "api.main:app" in src, "入口必须挂载 FastAPI 应用 api.main:app"
    assert "0.0.0.0" in src, "必须绑定 0.0.0.0，否则反向代理不可达"
    assert "PORT" in src, "必须读取平台注入的 PORT 环境变量"


def test_deploy_entry_does_not_start_server_on_import():
    """import main 不得启动服务（测试收集期会 import 全部模块）。"""
    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, "-c", "import main; print('imported')"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, f"import main 失败：{r.stderr[-300:]}"
    assert "imported" in r.stdout

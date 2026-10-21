"""公网部署入口（平台约定：根目录 main.py）。

背景（2026-10-21 实测）：发布平台对 Python 项目的自动探测执行
`python main.py`；本项目入口是 uvicorn 模块路径 `api.main:app`，
此前根目录无 main.py → 沙箱启动失败（SITES_START_FAILED），
分享链接随旧沙箱一并不可用。故补此入口，职责只有三件事：

  1. 读取平台注入的 PORT 环境变量（默认 3000，与沙箱约定一致）；
  2. 绑定 0.0.0.0 —— 只听 127.0.0.1 反向代理不可达；
  3. 挂载 api.main:app（FastAPI 单端口托管 SPA + /api）。

本地开发仍用文档命令（带 --reload）：
    python -m uvicorn api.main:app --reload --port 8000

密钥与口径全部走环境变量 / .env（见 docs/部署与密钥配置.md），
本文件不引入任何配置逻辑。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 防御：确保无论从哪个 cwd 启动（python main.py / python /workspace/main.py），
# 项目根都在 sys.path 上，`api.main:app` 可导入。
_ROOT = str(Path(__file__).resolve().parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def main() -> None:
    import uvicorn

    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "3000")),
    )


if __name__ == "__main__":
    main()

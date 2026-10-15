"""确保项目根目录在 sys.path 上，使 `import api` 可用。

另：测试进程强制 mock 模式（见下方环境变量处理）。
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 测试强制 mock 模式：在任何 `import api.*` 之前清空 LLM_API_KEY。
#
# 为什么必须在这里做：api.config 在 import 时就会读环境变量并求值 Settings
# 类属性，且 get_settings 带 lru_cache。若本地 .env 填了真实 Key，测试套件
# 会开始真实调用 LLM —— 慢、花钱、temperature=0 也无法保证逐字一致，
# 「测试可离线复现」的性质直接失效。
#
# 真实 LLM 链路由 scripts/e2e_smoke.py 与手动验证覆盖，不走单测。
os.environ["LLM_API_KEY"] = ""
os.environ.pop("LLM_BASE_URL", None)
os.environ.pop("LLM_MODEL", None)

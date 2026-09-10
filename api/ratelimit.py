"""轻量内存限流（单进程演示用）。

目的：防止 /api/upload、/api/analyze 被高频调用导致内存/磁盘/LLM 配额耗尽。

设计取舍：
- 按客户端 IP 分桶，滑动窗口计数，无第三方依赖（保证 10 分钟可复现）；
- 窗口内记录自动清理，不会像朴素字典那样只增不减；
- 单进程内存态，与 store.py 同理需单 worker 部署（见 README 部署约束）。
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections import deque
from typing import Deque


class RateLimiter:
    """滑动窗口限流器：窗口内最多放行 max_calls 次。"""

    def __init__(self, max_calls: int, window_seconds: float = 60.0) -> None:
        self.max_calls = max_calls
        self.window = window_seconds
        self._hits: dict[str, Deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        """是否放行；放行即记一次。"""
        now = time.time()
        q = self._hits[key]
        # 滑出窗口的记录直接丢弃，避免内存无限增长
        while q and (now - q[0]) > self.window:
            q.popleft()
        if len(q) >= self.max_calls:
            return False
        q.append(now)
        return True

    def retry_after(self, key: str) -> int:
        """距离可重试还需多少秒（用于 Retry-After 响应头）。"""
        q = self._hits.get(key)
        if not q or len(q) < self.max_calls:
            return 0
        return max(1, int(self.window - (time.time() - q[0])) + 1)

    def reset(self, key: str | None = None) -> None:
        """清空计数（测试用）。"""
        if key is None:
            self._hits.clear()
        else:
            self._hits.pop(key, None)


# 上传：解析开销大，单 IP 每分钟 10 次
upload_limiter = RateLimiter(max_calls=10, window_seconds=60.0)
# 分析：涉及 LLM 调用（有真实成本），单 IP 每分钟 20 次
analyze_limiter = RateLimiter(max_calls=20, window_seconds=60.0)
# 脚本生成：触发一次真实 LLM 调用，单 IP 每分钟 15 次（R-RL-01）
script_limiter = RateLimiter(max_calls=15, window_seconds=60.0)
# 报告导出：PDF/DOCX 走 WeasyPrint 重渲染，开销大，单 IP 每分钟 20 次（R-RL-01）
report_limiter = RateLimiter(max_calls=20, window_seconds=60.0)


def client_ip(request) -> str:
    """取客户端 IP；取不到时退回统一桶（仍能限流，只是粒度变粗）。"""
    try:
        return request.client.host if request.client else "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"

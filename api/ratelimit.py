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


def _daily_budget_from_env() -> int:
    """读 LLM_DAILY_BUDGET；用裸 os.environ 而非 config，避免模块循环依赖。"""
    import os

    try:
        return int(os.getenv("LLM_DAILY_BUDGET", "0"))
    except ValueError:
        return 0


class RateLimiter:
    """滑动窗口限流器：窗口内最多放行 max_calls 次。"""

    # 键数上限（第十二轮新增）：原实现只清理「当前请求 key」的 deque，
    # 不再回来的 IP 条目永不清理 —— 伪造源 IP 可让 _hits 无界增长（内存耗尽）。
    MAX_KEYS = 10000

    def __init__(self, max_calls: int, window_seconds: float = 60.0) -> None:
        self.max_calls = max_calls
        self.window = window_seconds
        self._hits: dict[str, Deque[float]] = defaultdict(deque)

    def _sweep(self, now: float) -> None:
        """全量清扫：删除已空/全部滑出窗口的键；仍超限则淘汰最旧的键。"""
        for key in [k for k, q in self._hits.items() if not q or (now - q[0]) > self.window]:
            self._hits.pop(key, None)
        while len(self._hits) >= self.MAX_KEYS:
            # deque 首元素即该键最旧记录；按全局最旧者淘汰
            oldest = min(self._hits, key=lambda k: self._hits[k][0])
            self._hits.pop(oldest, None)

    def allow(self, key: str) -> bool:
        """是否放行；放行即记一次。"""
        now = time.time()
        if len(self._hits) >= self.MAX_KEYS:
            self._sweep(now)
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
# 追问对话：多轮交互频率高且触发 LLM 调用，单 IP 每分钟 30 次
chat_limiter = RateLimiter(max_calls=30, window_seconds=60.0)


class DailyBudget:
    """按自然日的 LLM 调用总量闸门（成本兜底）。

    为什么已有按 IP 限流还不够：
    1. 反向代理后面 `request.client.host` 拿到的是代理 IP，所有访客共用一个桶
       ——限流实际退化成全局上限，且窗口一过（60s）就重新放开；
    2. 反代后的全局 20 次/分钟，理论上限≈2.9 万次/天，即使每次只调1 次 LLM，
       额度也会被跑穿。

    所以再加一道**按天**的总量闸门：max_calls<=0 表示不限（本地开发用），
    公网部署必须设置。它是最后一道成本刹车，与 IP 限流叠加使用。
    """

    def __init__(self, max_calls: int = 0) -> None:
        self.max_calls = max_calls
        self._used = 0
        self._day = time.strftime("%Y-%m-%d")

    def _rollover(self) -> None:
        today = time.strftime("%Y-%m-%d")
        if today != self._day:
            self._day, self._used = today, 0

    def allow(self) -> tuple[bool, int]:
        """返回 (是否放行, 剩余额度)。剩余为 -1 表示不限量。"""
        self._rollover()
        if self.max_calls <= 0:
            self._used += 1
            return True, -1
        if self._used >= self.max_calls:
            return False, 0
        self._used += 1
        return True, self.max_calls - self._used

    @property
    def remaining(self) -> int:
        self._rollover()
        return -1 if self.max_calls <= 0 else max(0, self.max_calls - self._used)

    @property
    def used(self) -> int:
        self._rollover()
        return self._used

    def reset(self) -> None:
        self._used = 0
        self._day = time.strftime("%Y-%m-%d")


# 登录接口限流（第十五轮，R-AUTH-03）：防在线爆破的第二道闸
# （第一道是 main.py 的连续失败锁定）。10 次/分钟/IP，成功失败都计数。
login_limiter = RateLimiter(max_calls=10, window_seconds=60.0)


# 全局 LLM 日配额；上限由环境变量 LLM_DAILY_BUDGET 决定（0 = 不限，本地默认）
llm_budget = DailyBudget(max_calls=_daily_budget_from_env())


def client_ip(request) -> str:
    """取客户端 IP；取不到时退回统一桶（仍能限流，只是粒度变粗）。"""
    try:
        return request.client.host if request.client else "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"

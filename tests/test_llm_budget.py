"""LLM 日配额闸门（公网挂真实 Key 时的成本刹车）。

背景：/api/analyze、/api/chat、/api/script 都会触发真实 LLM 调用。已有的
按 IP 限流在反向代理后实际退化为「全局每分钟 N 次」，窗口一过就放开，
挡不住长时间爬取。这组测试锁定按天总量闸门的行为。
"""

from __future__ import annotations

import pytest

from api.ratelimit import DailyBudget


def test_unlimited_when_max_is_zero():
    """0 = 不限（本地开发默认），连续调用不应被拦。"""
    b = DailyBudget(max_calls=0)
    for _ in range(50):
        ok, remaining = b.allow()
        assert ok is True
        assert remaining == -1, "不限量时 remaining 应为 -1"
    assert b.remaining == -1


def test_blocks_after_quota_exhausted():
    b = DailyBudget(max_calls=3)
    assert [b.allow() for _ in range(3)] == [(True, 2), (True, 1), (True, 0)]
    ok, remaining = b.allow()
    assert ok is False, "超出日配额必须拒绝"
    assert remaining == 0


def test_remaining_counts_down():
    b = DailyBudget(max_calls=2)
    b.allow()
    assert b.remaining == 1
    b.allow()
    assert b.remaining == 0


def test_reset_clears_usage():
    b = DailyBudget(max_calls=1)
    b.allow()
    assert b.allow()[0] is False
    b.reset()
    assert b.remaining == 1
    assert b.allow()[0] is True


def test_daily_rollover(monkeypatch: pytest.MonkeyPatch):
    """跨自然日自动重置——否则第二天起服务会一直 429。"""
    import api.ratelimit as rl

    b = DailyBudget(max_calls=1)
    b.allow()
    assert b.allow()[0] is False, "当日配额用尽后应拒绝"

    # 把 strftime 固定成另一个日期，模拟跨天
    monkeypatch.setattr(rl.time, "strftime", lambda fmt, *a, **kw: "2000-01-01")
    assert b.remaining == 1, "跨日后remaining 应恢复满额"
    assert b.allow()[0] is True, "跨日后应重新放行"


def test_env_value_is_read(monkeypatch: pytest.MonkeyPatch):
    """上限来自 LLM_DAILY_BUDGET 环境变量；非法值退回不限而不是崩溃。"""
    import importlib

    import api.ratelimit as rl

    monkeypatch.setenv("LLM_DAILY_BUDGET", "7")
    importlib.reload(rl)
    assert rl.llm_budget.max_calls == 7

    monkeypatch.setenv("LLM_DAILY_BUDGET", "not-a-number")
    importlib.reload(rl)
    assert rl.llm_budget.max_calls == 0, "非法值应退回不限，不能让服务起不来"

    monkeypatch.delenv("LLM_DAILY_BUDGET", raising=False)
    importlib.reload(rl)
    assert rl.llm_budget.max_calls == 0
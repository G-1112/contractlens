"""Task #11 回归测试：LLM 并发判定 + 重试分类。

- 重试：temperature=0 下结构非法（确定性错误）直接降级，不重试；
        网络/超时类错误才重试，重试耗尽后由 fusion 降级。
- 并发：gather 保持条款顺序提交，N 条判定墙钟耗时远低于串行（约 8x）；
        流式路径下 judge 阶段恰好推送 total 次进度事件。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from api.config import Settings
from api.engine.llm_engine import LlmEngine, LLMUnavailable
from api.pipeline import run_analysis, run_analysis_streaming
from api.schemas import Clause, DocumentLine, LlmJudgement, ParsedDocument


# --------------------------------------------------------------------------- #
# 重试分类（直接测 LlmEngine.judge，注入假客户端，避免依赖 openai 包）           #
# --------------------------------------------------------------------------- #
class _Resp:
    def __init__(self, content: str):
        msg = type("M", (), {"content": content})()
        self.choices = [type("C", (), {"message": msg})()]


def _ok(resp):
    async def _c(**_kw):
        return resp

    return _c()


def _raise(exc):
    async def _c(**_kw):
        raise exc

    return _c()


class _FakeCompletions:
    def __init__(self, behavior):
        self._behavior = behavior  # "ok" | "bad" | "network"
        self.calls = 0

    def create(self, **_kw):
        self.calls += 1
        if self._behavior == "ok":
            return _ok(_Resp('{"risk_level": "无"}'))
        if self._behavior == "bad":
            # 非法 risk_level → pydantic ValidationError（确定性错误）
            return _ok(_Resp('{"risk_level": "未知等级"}'))
        return _raise(RuntimeError("connection reset"))


class _FakeClient:
    def __init__(self, behavior):
        self.chat = type("Chat", (), {"completions": _FakeCompletions(behavior)})()


def _engine(behavior: str, retries: int = 2) -> tuple[LlmEngine, _FakeCompletions]:
    s = Settings()
    s.llm_api_key = ""  # 不启用真实客户端分支（本环境无 openai 包）
    eng = LlmEngine(s)  # __init__ 不会 import openai
    eng._client = _FakeClient(behavior)  # 直接注入假客户端
    eng.settings.llm_max_retries = retries
    return eng, eng._client.chat.completions


def _clause(cid: str) -> Clause:
    return Clause(id=cid, text=f"第{cid}条 普通约定条款。", page=1)


def test_deterministic_error_not_retried():
    """结构非法（temperature=0）直接降级，不再浪费重试配额。"""
    eng, comp = _engine("bad")
    with pytest.raises(LLMUnavailable):
        asyncio.run(eng.judge(_clause("c1"), []))
    assert comp.calls == 1, "确定性错误不应重试，应仅调用 1 次"


def test_network_error_retries_then_degrades():
    """网络错误按 max_retries 重试，耗尽后抛 LLMUnavailable 降级。"""
    eng, comp = _engine("network", retries=2)
    with pytest.raises(LLMUnavailable):
        asyncio.run(eng.judge(_clause("c1"), []))
    assert comp.calls == 3, "网络错误应重试 max_retries+1=3 次"


# --------------------------------------------------------------------------- #
# 并发判定（pipeline 层，真实加速比 + 进度事件计数）                            #
# --------------------------------------------------------------------------- #
def _build_parsed(n: int) -> ParsedDocument:
    lines = [
        DocumentLine(text=f"第{i}条 甲方有权提前通知乙方调整租金，押金概不退还。", page=1)
        for i in range(1, n + 1)
    ]
    return ParsedDocument(
        lines=lines,
        clauses=[Clause(id=f"c{i}", text=ln.text, page=1) for i, ln in enumerate(lines, 1)],
        page_count=1,
        contract_type="rent",
        has_text_layer=True,
    )


def test_concurrent_speedup(monkeypatch):
    """并发=8 时 40 条判定的墙钟耗时显著低于串行（约 8x 加速）。"""
    per_call = 0.05
    calls: list[int] = []

    async def _fake_judge(self, clause, regs):
        calls.append(1)
        await asyncio.sleep(per_call)
        return LlmJudgement(clause_id=clause.id, risk_level="无")

    # 直接 patch 类对象（与 pipeline 引用的同一 LlmEngine）
    from api.engine import llm_engine as le_mod
    from api.config import get_settings

    monkeypatch.setattr(le_mod.LlmEngine, "judge", _fake_judge)

    parsed = _build_parsed(40)

    def _run(concurrency: int) -> float:
        # 必须用流水线实际读取的缓存单例 get_settings()，而非新建 Settings()
        get_settings().llm_concurrency = concurrency
        t0 = time.perf_counter()
        asyncio.run(run_analysis(parsed, "rent", "t"))
        return time.perf_counter() - t0

    serial = _run(1)
    parallel = _run(8)

    assert len(calls) == 80, f"假 judge 应被调用 80 次（两次各 40，实测 {len(calls)}）"
    # 40 × 0.05s = 2.0s 串行；并发=8 应 < 1.0s（≈0.25s），给足余量
    assert parallel < 1.0, f"并发=8 时 40 条应远快于串行，实测 {parallel:.2f}s"
    assert parallel < serial * 0.5, f"应至少 2x 加速（实测 {serial / parallel:.1f}x）"


def test_judge_progress_event_count(monkeypatch):
    """流式路径下 judge 阶段恰好推送 total 次进度事件（并发完成计数正确）。"""
    events: list[dict] = []

    async def _fake_judge(clause, regs):
        await asyncio.sleep(0.001)
        return LlmJudgement(clause_id=clause.id, risk_level="无")

    monkeypatch.setattr("api.engine.llm_engine.LlmEngine.judge", _fake_judge)

    parsed = _build_parsed(40)
    asyncio.run(
        run_analysis_streaming(parsed, "rent", "t", emit=lambda e: events.append(e))
    )

    judge_events = [e for e in events if e.get("stage") == "judge"]
    assert len(judge_events) == 40, "judge 阶段应恰好推送 40 次进度"
    # 末次进度 current 应为 total
    assert judge_events[-1]["current"] == 40


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

"""性能基线：分段计时，定位端到端瓶颈（大赛「性能优化」实证依据）。

把借款合同样本重复 N 次拼成大文档，分别测量：
  解析（docx → 条款树） / 规则引擎 / 端到端分析
每项跑 R 次取中位数，输出 markdown 表格。

用法：python scripts/benchmark.py [--repeat 30] [--rounds 3]
依赖：python-docx（构造大样本）
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from api.engine import RuleEngine  # noqa: E402
from api.pipeline import parse_file, run_analysis  # noqa: E402

try:
    from docx import Document
except ImportError:  # pragma: no cover
    raise SystemExit("请先安装 python-docx：pip install python-docx")

SAMPLE = ROOT / "data" / "samples" / "借款合同样本.txt"
TMP = ROOT / "data" / "uploads" / "_bench_large.docx"


def build_large(repeat: int) -> Path:
    """把样本条款重复 repeat 次，构造大 DOCX（模拟长合同）。"""
    lines = SAMPLE.read_text(encoding="utf-8").splitlines()
    body = [ln for ln in lines if ln.strip()]
    doc = Document()
    for i in range(repeat):
        for ln in body:
            doc.add_paragraph(ln)
    TMP.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(TMP))
    return TMP


def timed(fn, rounds: int) -> float:
    """跑 rounds 次，返回中位数耗时（毫秒）。"""
    samples: list[float] = []
    for _ in range(rounds):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return round(statistics.median(samples), 1)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=30, help="样本重复次数（默认 30 → 约 330 条款）")
    ap.add_argument("--rounds", type=int, default=3, help="每项计时轮数")
    args = ap.parse_args()

    path = build_large(args.repeat)
    print(f"大文档：{path.name}（{path.stat().st_size / 1024:.1f} KB）")

    # 预热（首次含模块导入与规则库加载，不计入基线）
    parsed0 = parse_file(path, path.name, "loan")
    await run_analysis(parsed0, "loan", "bench-warmup")

    t_parse = timed(lambda: parse_file(path, path.name, "loan"), args.rounds)
    parsed = parse_file(path, path.name, "loan")
    n_clauses = len(parsed.clauses)

    def run_rule() -> None:
        e = RuleEngine().load("loan")
        e.match_clauses(parsed.clauses)
        e.match_document(parsed.clauses)

    t_rule = timed(run_rule, args.rounds)

    async def run_e2e() -> None:
        await run_analysis(parsed, "loan", "bench-e2e")

    async def timed_async(coro_factory, rounds: int) -> float:
        samples = []
        for _ in range(rounds):
            t0 = time.perf_counter()
            await coro_factory()
            samples.append((time.perf_counter() - t0) * 1000)
        return round(statistics.median(samples), 1)

    t_e2e = await timed_async(run_e2e, args.rounds)

    print()
    print("| 阶段 | 条款数 | 耗时中位数(ms) | 单条款(ms) | 占比 |")
    print("|---|---|---|---|---|")
    rows = [("解析（文本→条款树）", t_parse), ("规则引擎", t_rule), ("端到端分析（含检索+判定+融合+评分）", t_e2e)]
    for name, ms in rows:
        per = round(ms / n_clauses, 3) if n_clauses else 0
        pct = f"{ms / t_e2e * 100:.1f}%" if t_e2e else "—"
        print(f"| {name} | {n_clauses} | {ms} | {per} | {pct} |")
    print()
    print(f"注：端到端为 mock 模式（无 LLM Key），判定阶段受 asyncio 信号量并发度约束。")


if __name__ == "__main__":
    asyncio.run(main())

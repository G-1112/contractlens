"""端到端流水线测试（mock 模式，无外部依赖）。"""

import asyncio
from pathlib import Path

import pytest

from api.parser import split_clauses
from api.pipeline import run_analysis, run_analysis_streaming
from api.schemas import DocumentLine, ParsedDocument

SAMPLES = Path(__file__).resolve().parent.parent / "data" / "samples"


def _load(name: str, contract_type: str) -> ParsedDocument:
    text = (SAMPLES / name).read_text(encoding="utf-8")
    lines = [
        DocumentLine(text=ln.strip(), page=1) for ln in text.splitlines() if ln.strip()
    ]
    clauses = split_clauses(lines)
    return ParsedDocument(
        lines=lines,
        clauses=clauses,
        page_count=1,
        contract_type=contract_type,
        has_text_layer=True,
    )


def _run(parsed: ParsedDocument):
    return asyncio.run(run_analysis(parsed, parsed.contract_type, "test"))


def test_rent_pipeline_finds_risks():
    parsed = _load("房屋租赁合同样本.txt", "rent")
    report = _run(parsed)
    assert report.items, "应命中风险条款"
    levels = {it.final_level for it in report.items}
    assert "高" in levels
    # 押金三个月 → R01 高；押金概不退还 → R02 高
    assert report.total_score < 60
    assert report.talk_scripts, "应生成谈判话术"


def test_labor_pipeline_finds_risks():
    parsed = _load("劳动合同样本.txt", "labor")
    report = _run(parsed)
    assert report.items
    # 试用期六个月 → L01 高；试用期工资 60% → L02 高
    assert report.total_score < 60


def test_intern_pipeline_finds_risks():
    parsed = _load("实习协议样本.txt", "intern")
    report = _run(parsed)
    assert report.items


@pytest.mark.parametrize(
    "name,ctype",
    [
        ("房屋租赁合同样本.txt", "rent"),
        ("劳动合同样本.txt", "labor"),
        ("实习协议样本.txt", "intern"),
    ],
)
def test_radar_dimensions_present(name: str, ctype: str):
    parsed = _load(name, ctype)
    report = _run(parsed)
    assert len(report.radar_dimensions) == 5
    assert set(report.radar.keys()) == set(report.radar_dimensions)


def test_clause_overflow_truncated(monkeypatch):
    """条款超限：按文档顺序只分析前 N 条，SSE 提示截断，report 带 truncated 标志。"""
    from api.config import get_settings
    from api.schemas import Clause

    monkeypatch.setattr(get_settings(), "analyze_max_clauses", 5, raising=False)

    clauses = [
        Clause(id=f"c-{i:03d}", text=f"第{chr(0x4E00 + i)}条 甲方应按时支付租金。", page=1)
        for i in range(20)
    ]
    parsed = ParsedDocument(
        lines=[DocumentLine(text=c.text, page=1) for c in clauses],
        clauses=clauses,
        page_count=1,
        contract_type="rent",
        has_text_layer=True,
    )

    events: list[dict] = []
    report = asyncio.run(
        run_analysis_streaming(parsed, "rent", "test-truncate", events.append)
    )

    assert report.truncated is True
    assert report.clause_count == 5
    # 判定进度总量应为截断后的 5 条
    judge_events = [e for e in events if e["type"] == "progress" and e["stage"] == "judge"]
    assert judge_events and judge_events[0]["total"] == 5
    # 截断提示事件
    assert any(
        e["type"] == "progress" and "条款超限，已截断为前 5 条" in e.get("message", "")
        for e in events
    )
    # 风险条目只可能来自前 5 条（缺失检测为文档级，clause_id 以 missing- 开头）
    assert all(
        it.clause_id in {c.id for c in clauses[:5]} or it.clause_id.startswith("missing-")
        for it in report.items
    )


def test_clause_under_limit_not_truncated():
    """未超限时不截断、无提示、truncated=False。"""
    events: list[dict] = []
    parsed = _load("房屋租赁合同样本.txt", "rent")
    report = asyncio.run(
        run_analysis_streaming(parsed, "rent", "test-no-truncate", events.append)
    )
    assert report.truncated is False
    assert not any("截断" in e.get("message", "") for e in events)

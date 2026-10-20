"""端到端流水线测试（mock 模式，无外部依赖）。"""

import asyncio
from pathlib import Path

import pytest

from api.pipeline import run_analysis
from api.parser import split_clauses
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


def test_skip_talk_scripts_keeps_scoring_intact():
    """generate_scripts=False：话术为空，但风险条目与总分必须与内联路径一致。

    背景：真实 LLM 下话术生成是第二个 LLM 波次（实测 3~5s），内联会显著推后
    SSE `done` 事件。改为报告页按需调POST /api/script 后，必须保证分析结果本身
    不受影响——否则「更快拿到报告」会以牺牲结论完整性为代价。
    """
    parsed = _load("房屋租赁合同样本.txt", "rent")
    with_scripts = _run(parsed)
    without_scripts = asyncio.run(
        run_analysis(parsed, parsed.contract_type, "test", generate_scripts=False)
    )

    assert without_scripts.talk_scripts == [], "跳过话术时应为空，由前端按需补生成"
    assert without_scripts.total_score == with_scripts.total_score
    assert len(without_scripts.items) == len(with_scripts.items)
    assert [it.clause_id for it in without_scripts.items] == [
        it.clause_id for it in with_scripts.items
    ]
    assert [it.final_level for it in without_scripts.items] == [
        it.final_level for it in with_scripts.items
    ]

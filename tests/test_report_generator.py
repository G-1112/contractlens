"""报告与话术生成测试（FR-10 / FR-11 / FR-12）。

覆盖 markdown / html / docx 导出，以及话术模板与并发生成。
PDF 导出依赖 weasyprint（本环境未安装，单独由 save_pdf 的导入错误路径覆盖或跳过）。
"""

from __future__ import annotations

import asyncio

from api.report.generator import (
    ReportGenerator,
    _extract_ref,
    _template_script,
    generate_talk_scripts,
)
from api.schemas import AnalysisReport, RiskItem


def _risk(level="高", clause_text="第五条 押金概不退还", clause_id="c1", **kw):
    return RiskItem(clause_id=clause_id, clause_text=clause_text, final_level=level, **kw)


def _report() -> AnalysisReport:
    items = [
        _risk(
            "高",
            "第五条 押金概不退还",
            risk_type="押金",
            explain="押金不退",
            suggest="建议明确退还条件",
            rewrite="租赁期满押金无息退还",
            source="both",
        ),
        _risk(
            "中",
            "第六条 甲方有权单方面涨租",
            risk_type="涨租",
            explain="单方涨租",
            suggest="建议协商",
            rewrite="经协商一致方可调整",
            source="rule_override",
            needs_review=True,
        ),
    ]
    return AnalysisReport(
        file_id="f1",
        contract_type="rent",
        total_score=60,
        overall_level="中",
        items=items,
        clause_count=2,
        risk_counts={"高": 1, "中": 1, "低": 0, "无": 0},
    )


def test_to_markdown():
    md = ReportGenerator(_report()).to_markdown()
    assert "# 合同审查报告" in md
    assert "60 / 100" in md
    assert "押金" in md
    assert "免责声明" in md


def test_save_markdown(tmp_path):
    out = ReportGenerator(_report()).save_markdown(tmp_path / "r.md")
    assert out.exists()
    assert "合同审查报告" in out.read_text(encoding="utf-8")


def test_save_docx(tmp_path):
    out = ReportGenerator(_report()).save_docx(tmp_path / "r.docx")
    assert out.exists()


def test_to_html():
    html = ReportGenerator(_report()).to_html()
    assert "合同审查报告" in html
    assert "60 / 100" in html
    assert "押金" in html


def test_extract_ref():
    assert _extract_ref(RiskItem(clause_id="c1", clause_text="第五条 押金概不退还")) == "五条"
    assert _extract_ref(RiskItem(clause_id="c1", clause_text="无编号条款", page=3)) == "3"


def test_template_script():
    s = _template_script(
        RiskItem(clause_id="c1", clause_text="第五条 押金概不退还", suggest="明确退还条件", rewrite="押金无息退还")
    )
    assert "押金" in s


def test_generate_talk_scripts_empty():
    assert asyncio.run(generate_talk_scripts([], None)) == []


def test_generate_talk_scripts_template():
    items = [_risk("高", "第五条 押金概不退还", suggest="X", rewrite="Y")]
    scripts = asyncio.run(generate_talk_scripts(items, None))
    assert len(scripts) == 1
    assert "押金" in scripts[0]


def test_generate_talk_scripts_no_targets():
    # 无高/中风险条款 → 不生成话术
    items = [_risk("低", "第九条 未经甲方同意不得转租")]
    assert asyncio.run(generate_talk_scripts(items, None)) == []


def test_generate_talk_scripts_priority_order():
    # 高 > 中 优先，最多 5 条
    items = [
        _risk("中", "第六条 违约金条款", clause_id="c2"),
        _risk("高", "第五条 押金概不退还", clause_id="c1"),
        _risk("中", "第七条 转租条款", clause_id="c3"),
        _risk("中", "第八条 维修条款", clause_id="c4"),
        _risk("中", "第十条 解除条款", clause_id="c5"),
        _risk("中", "第十一条 费用条款", clause_id="c6"),
    ]
    scripts = asyncio.run(generate_talk_scripts(items, None))
    assert len(scripts) == 5  # 截断到 5 条
    assert "押金" in scripts[0]  # 高风险排最前

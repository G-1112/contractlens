"""条款树切分测试（FR-03）。"""

from api.parser.clause_splitter import split_clauses
from api.schemas import DocumentLine


def _lines(*texts: str) -> list[DocumentLine]:
    return [DocumentLine(text=t, page=1) for t in texts]


def test_split_chinese_articles():
    lines = _lines(
        "第一条 押金为三个月租金。",
        "第二条 若提前退租，押金概不退还。",
        "第三条 甲方有权单方面提高租金。",
    )
    clauses = split_clauses(lines)
    assert len(clauses) == 3
    assert all(c.level == 2 for c in clauses)
    # id 稳定：c-<页码>-<序号>
    assert clauses[0].id == "c-001-01"
    assert "押金为三个月租金" in clauses[0].text


def test_continuation_lines_merged_into_previous():
    """无编号的连续行归入上一条。"""
    lines = _lines("第一条 租赁期限为 12 个月。", "本合同自 2026 年 1 月 1 日生效。")
    clauses = split_clauses(lines)
    assert len(clauses) == 1
    assert "租赁期限" in clauses[0].text
    assert "2026 年 1 月 1 日" in clauses[0].text


def test_chapter_and_article_hierarchy():
    lines = _lines(
        "第一章 总则",
        "第一条 租赁期限为 12 个月。",
        "第二条 租金为每月 3000 元。",
    )
    clauses = split_clauses(lines)
    assert clauses[0].level == 1  # 章
    assert clauses[1].level == 2  # 条
    assert clauses[1].parent == clauses[0].id


def test_arabic_numbering():
    lines = _lines("1.1 适用范围", "1.2 定义与解释")
    clauses = split_clauses(lines)
    assert len(clauses) == 2
    assert clauses[0].level == 3


def test_empty_lines_ignored():
    lines = _lines("", "第一条 押金。", "   ")
    clauses = split_clauses(lines)
    assert len(clauses) == 1

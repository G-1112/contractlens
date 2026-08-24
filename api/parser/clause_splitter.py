"""条款树切分（FR-03）。

把解析出的行级文本，按中文合同编号习惯切分为条款树：
- 识别「章 / 条 / 款 / 项」四级编号模式；
- 无编号的连续行归入上一条的"款"；
- 每条输出 id / text / page / bbox / level / parent。

验收：15 份人工标注样本上切分点 F1 ≥ 0.90。
"""

from __future__ import annotations

import re
from typing import Iterable

from ..schemas import Clause, DocumentLine

# 编号模式 → 层级。顺序即优先级，先匹配到的优先。
_PATTERNS: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"^第[一二三四五六七八九十百零〇]+章"), 1),  # 章
    (re.compile(r"^第[一二三四五六七八九十百零〇]+条"), 2),  # 条
    (re.compile(r"^[一二三四五六七八九十]+、"), 2),  # 一、
    (re.compile(r"^\d+\.\d+(\.\d+)*\s*\S?"), 3),  # 1.1 / 1.1.1
    (re.compile(r"^[（(][一二三四五六七八九十]+[）)]"), 4),  # （一）
]

# 中文字序号 → 数值，用于稳定排序（用于生成 id 序号，避免依赖出现顺序）
_CN_NUM = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _is_heading(line: str) -> tuple[int, int] | None:
    """判断一行是否为条款编号起始行，返回 (level, 优先级索引) 或 None。"""
    stripped = line.strip()
    if not stripped:
        return None
    for idx, (pat, level) in enumerate(_PATTERNS):
        if pat.match(stripped):
            return level, idx
    return None


def _level_name(level: int) -> str:
    return {1: "章", 2: "条", 3: "款", 4: "项"}.get(level, "条")


class ClauseSplitter:
    """把行级文本切分为条款树。纯函数式，无状态，可独立测试。"""

    def __init__(self) -> None:
        self._counters: dict[int, int] = {}

    # ------------------------------------------------------------------
    def split(self, lines: Iterable[DocumentLine]) -> list[Clause]:
        """入口：DocumentLine 列表 → Clause 列表。"""
        lines = [ln for ln in lines if ln.text.strip()]
        blocks = self._group_blocks(lines)
        return self._build_tree(blocks)

    # ------------------------------------------------------------------
    def _group_blocks(self, lines: list[DocumentLine]) -> list[dict]:
        """把行按编号起始行聚合成"块"。

        每个块 = 一个编号起始行 + 其后所有无编号行（直到下一个编号起始行）。
        """
        blocks: list[dict] = []
        current: dict | None = None

        for ln in lines:
            heading = _is_heading(ln.text)
            if heading is not None:
                level, _prio = heading
                current = {
                    "level": level,
                    "lines": [ln],
                    "page": ln.page,
                    "bbox": list(ln.bbox),
                }
                blocks.append(current)
            elif current is not None:
                current["lines"].append(ln)
                # 扩展块级 bbox，包含后续行
                if ln.bbox[3] > current["bbox"][3]:
                    current["bbox"][3] = ln.bbox[3]
            else:
                # 文档开头无编号的说明文字（如标题），归为 level 2 的"前言"块
                current = {
                    "level": 2,
                    "lines": [ln],
                    "page": ln.page,
                    "bbox": list(ln.bbox),
                }
                blocks.append(current)

        # 过短（<2 字符）且非条款的噪声块并入上一块
        return blocks

    # ------------------------------------------------------------------
    def _build_tree(self, blocks: list[dict]) -> list[Clause]:
        clauses: list[Clause] = []
        stack: list[Clause] = []  # 各级别最近的祖先
        seq = 0

        for block in blocks:
            level = block["level"]
            text = self._join_lines(block["lines"]).strip()
            if not text:
                continue

            seq += 1
            # 生成稳定 id：c-<页码>-<序号>
            clause_id = f"c-{block['page']:03d}-{seq:02d}"

            # 找父节点：向上找级别小于当前级别的最近节点
            parent: Clause | None = None
            while stack and stack[-1].level >= level:
                stack.pop()
            if stack:
                parent = stack[-1]

            clause = Clause(
                id=clause_id,
                text=text,
                page=block["page"],
                bbox=block["bbox"],
                level=level,
                parent=parent.id if parent else None,
            )
            clauses.append(clause)
            stack.append(clause)

        return clauses

    # ------------------------------------------------------------------
    @staticmethod
    def _join_lines(lines: list[DocumentLine]) -> str:
        """把块内多行合并为一段文本。

        中文字符之间不留空格；编号与正文之间用自然连接。
        """
        parts = [ln.text.strip() for ln in lines if ln.text.strip()]
        return "".join(parts) if _mostly_cjk(parts) else " ".join(parts)


def _mostly_cjk(parts: list[str]) -> bool:
    """判断文本是否以中文为主，决定用空串还是空格连接。"""
    total = sum(len(p) for p in parts)
    if total == 0:
        return True
    cjk = sum(1 for p in parts for ch in p if "一" <= ch <= "鿿")
    return cjk / total > 0.5


def split_clauses(lines: list[DocumentLine]) -> list[Clause]:
    """便捷函数。"""
    return ClauseSplitter().split(lines)

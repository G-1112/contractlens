"""分析流水线编排。

上传文件 → 解析 → 条款切分 → RAG 检索 → 规则/LLM 双引擎判定 → 融合
→ 评分/雷达 → 谈判话术，逐条以 SSE 事件推送进度与结果。

本模块是唯一的编排层，解析层与引擎层均可独立测试。
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator, Callable

from .config import get_settings
from .engine import FusionEngine, LlmEngine, RegulationRetriever, RuleEngine
from .engine.scoring import score_report
from .parser import DocxParser, PdfParser, split_clauses
from .report.generator import generate_talk_scripts
from .schemas import (
    AnalysisReport,
    Clause,
    DocumentLine,
    LlmJudgement,
    ParsedDocument,
    RiskItem,
    RuleHit,
)

# SSE 事件类型
Event = dict
Sink = Callable[[Event], object]


def parse_file(path: str | Path, filename: str, contract_type: str) -> ParsedDocument:
    """按扩展名选择解析器，输出统一中间结构 ParsedDocument。"""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        lines, has_text = PdfParser().parse(path)
    elif suffix in (".docx", ".doc"):
        lines, has_text = DocxParser().parse(path)
    else:
        raise ValueError(f"不支持的文件类型：{suffix}（仅支持 PDF / DOCX）")

    clauses = split_clauses(lines)
    return ParsedDocument(
        lines=lines,
        clauses=clauses,
        page_count=max((ln.page for ln in lines), default=1),
        contract_type=contract_type,  # type: ignore[arg-type]
        has_text_layer=has_text,
    )


def detect_contract_type(filename: str) -> str:
    """根据文件名/内容粗判合同类型（前端可选，后端兜底 rent）。"""
    name = filename.lower()
    if any(k in name for k in ("劳动", "labor", "合同", "用工")):
        return "labor"
    if any(k in name for k in ("实习", "intern")):
        return "intern"
    return "rent"


def _run_rule_engine(clauses: list[Clause], contract_type: str) -> list[RuleHit]:
    engine = RuleEngine().load(contract_type)
    hits = engine.match_clauses(clauses)
    hits += engine.match_document(clauses)
    return hits


def _build_missing_items(clauses: list[Clause], missing_hits: list[RuleHit]) -> list[RiskItem]:
    """缺失检测（文档级）→ 合成风险条目（无对应条款，用说明文字占位）。"""
    items: list[RiskItem] = []
    for h in missing_hits:
        items.append(
            RiskItem(
                clause_id=h.clause_id,
                clause_text=f"（缺失项）{h.explanation}",
                page=1,
                final_level=h.level,
                source="rule_only",
                risk_type="缺失",
                explain=h.explanation,
                suggest=f"建议补充约定：{h.explanation}",
                rewrite="",
                regulation_refs=[],
                rule_ids=[h.rule_id],
            )
        )
    return items


async def run_analysis_streaming(
    parsed: ParsedDocument, contract_type: str, file_id: str, emit: Sink | None = None
) -> AnalysisReport:
    """主流水线，逐条推送 SSE 事件，返回最终报告。"""
    settings = get_settings()
    clauses = parsed.clauses
    total = len(clauses)

    emit = emit or (lambda _e: None)

    # 1) 规则引擎（确定性，先跑，作为兜底）
    emit({"type": "progress", "stage": "rule", "current": 0, "total": total})
    rule_hits = _run_rule_engine(clauses, contract_type)
    rule_by_clause: dict[str, list[RuleHit]] = {}
    for h in rule_hits:
        rule_by_clause.setdefault(h.clause_id, []).append(h)
    missing_hits = [h for h in rule_hits if h.clause_id.startswith("missing-")]

    # 2) 检索器 + LLM 判定（逐条）
    retriever = RegulationRetriever()
    llm = LlmEngine(settings)
    fusion = FusionEngine()

    judgements: list[LlmJudgement] = []
    reg_refs: dict[str, list[str]] = {}

    for i, clause in enumerate(clauses, start=1):
        emit({"type": "progress", "stage": "judge", "current": i, "total": total})
        # RAG 检索（仅对命中规则或非空条款检索，控制成本）
        if clause.text.strip():
            refs = retriever.retrieve_for(clause, contract_type, top_k=settings.rag_top_k)
            reg_refs[clause.id] = refs
        else:
            refs = []
        # LLM 语义判定；失败降级（该条款由 fusion 走 rule_only）
        try:
            j = await llm.judge(clause, refs)
        except Exception:  # noqa: BLE001 LLM 不可用 → 记空，交由规则兜底
            j = LlmJudgement(clause_id=clause.id, risk_level="无")
        judgements.append(j)

    # 3) 融合 + 逐条推送风险事件
    items = fusion.fuse(clauses, [h for h in rule_hits if not h.clause_id.startswith("missing-")], judgements, reg_refs)
    items += _build_missing_items(clauses, missing_hits)

    for it in items:
        if it.final_level != "无":
            emit({"type": "risk", "data": it.model_dump()})

    # 4) 评分 + 话术
    emit({"type": "progress", "stage": "score", "current": total, "total": total})
    scripts = await generate_talk_scripts(items, llm)
    report = score_report(file_id, contract_type, items, scripts, clause_count=total)

    emit({"type": "done", "report": report.model_dump()})
    return report


async def run_analysis(parsed: ParsedDocument, contract_type: str, file_id: str) -> AnalysisReport:
    """非流式入口（测试/脚本用），内部复用流式实现。"""
    return await run_analysis_streaming(parsed, contract_type, file_id, emit=None)

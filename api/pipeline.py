"""分析流水线编排。

上传文件 → 解析 → 条款切分 → RAG 检索 → 规则/LLM 双引擎判定 → 融合
→ 评分/雷达 → 谈判话术，逐条以 SSE 事件推送进度与结果。

本模块是唯一的编排层，解析层与引擎层均可独立测试。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncIterator, Callable

from .config import get_settings
from .engine import FusionEngine, LlmEngine, RegulationRetriever, RuleEngine
from .engine.llm_engine import detect_injection
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


class _JudgeProgress:
    """并发判定阶段的进度计数：多条任务同时完成，用锁保证计数与推送原子。"""

    def __init__(self, total: int, emit: Sink) -> None:
        self._total = total
        self._done = 0
        self._emit = emit
        self._lock = asyncio.Lock()

    async def tick(self) -> None:
        async with self._lock:
            self._done += 1
            self._emit(
                {
                    "type": "progress",
                    "stage": "judge",
                    "current": self._done,
                    "total": self._total,
                }
            )


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
    """根据文件名/内容粗判合同类型（前端可选，后端兜底 rent）。

    第十二轮修复（潜伏 bug）：原实现把通用词「合同」当成 labor 关键词，
    「房屋租赁合同.docx」被误判为 labor。判断顺序改为 实习 → 劳动（去掉
    通用词「合同」，保留「劳务」）→ 租赁 → 兜底 rent。
    """
    name = filename.lower()
    if any(k in name for k in ("实习", "intern")):
        return "intern"
    if any(k in name for k in ("劳动", "labor", "用工", "劳务")):
        return "labor"
    # 金融类（大赛方向）：保证/担保优先于借款判定，
    # 因为「担保合同」「保证合同」常同时含「借款」字样。
    if any(k in name for k in ("担保", "保证", "guarantee")):
        return "guarantee"
    if any(k in name for k in ("借款", "借贷", "贷款", "loan", "借据", "欠条")):
        return "loan"
    if any(k in name for k in ("租", "rent")):
        return "rent"
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


def _build_injection_items(clauses: list[Clause], existing: list[RiskItem]) -> list[RiskItem]:
    """提示注入预检（R-INJ-01，大赛创新点）。

    与风险判定解耦：无论该条款是否被判为风险，只要命中注入特征就合成一条
    「注入警示」条目。原因（实测发现）：注入文本常藏在无风险条款里（如免责
    声明末尾的白色小字），若只在 llm judgement 上打标，而该条款未被判风险
    就不会生成 item，文档级 injection_flagged 会漏报 —— 演示时评委看不到
    任何警示。这里做全量扫描兜底。
    """
    seen = {it.clause_id for it in existing}
    items: list[RiskItem] = []
    for c in clauses:
        if c.id in seen:
            continue
        hits = detect_injection(c.text)
        if not hits:
            continue
        frag = "、".join(hits[:3])
        items.append(
            RiskItem(
                clause_id=c.id,
                clause_text=c.text[:200],
                page=c.page,
                final_level="中",
                source="rule_only",
                risk_type="注入风险",
                explain=(
                    f"该条款包含疑似提示注入内容（{frag}），可能试图操纵 AI 判定结果。"
                    "系统已对该内容做隔离处理，本条判定结果仅作参考，请人工复核。"
                ),
                suggest="删除合同中与正文无关的指令性文字后重新上传；如系对方提供，建议要求其说明该内容来源。",
                rewrite="",
                regulation_refs=[],
                rule_ids=[],
                injection_suspected=True,
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

    # 2) 检索器 + LLM 判定（并发，受信号量限流，加速比约 N/concurrency）
    retriever = RegulationRetriever()
    llm = LlmEngine(settings)
    fusion = FusionEngine()

    judgements: list[LlmJudgement] = []
    reg_refs: dict[str, list[str]] = {}

    sem = asyncio.Semaphore(max(1, settings.llm_concurrency))
    progress = _JudgeProgress(total, emit)

    async def _judge_one(clause: Clause) -> LlmJudgement | None:
        async with sem:
            # 第十二轮修复（实证 P1）：
            # ① RAG 检索原本在 try 之外 —— 检索器异常（如 USE_VECTOR_DB=true 时
            #    的递归崩溃）会直接击穿 gather，整份合同分析失败；
            # ② LLM 失败时原本返回 risk_level="无" 的空 judgement —— 但 fusion 的
            #    rule_only 分支要求 llm is None，空 judgement 让所有规则命中条目
            #    被打成 rule_override + 误导性文案「规则引擎判定为更高风险」
            #   （实为 LLM 失败，并非规则与 LLM 冲突）。返回 None 让 fusion 走
            #    真正的 rule_only。
            try:
                if clause.text.strip():
                    refs = retriever.retrieve_for(clause, contract_type, top_k=settings.rag_top_k)
                    reg_refs[clause.id] = refs
                else:
                    refs = []
                return await llm.judge(clause, refs)
            except Exception:  # noqa: BLE001 检索/LLM 不可用 → 记空，交由规则兜底（rule_only）
                return None

    async def _judge_and_tick(clause: Clause) -> LlmJudgement | None:
        j = await _judge_one(clause)
        await progress.tick()
        return j

    # gather 保持提交顺序，结果与 clauses 一一对应（供 fusion 使用）
    judgements = await asyncio.gather(*[_judge_and_tick(c) for c in clauses])

    # 3) 融合 + 逐条推送风险事件
    items = fusion.fuse(clauses, [h for h in rule_hits if not h.clause_id.startswith("missing-")], judgements, reg_refs)
    items += _build_missing_items(clauses, missing_hits)
    items += _build_injection_items(clauses, items)

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

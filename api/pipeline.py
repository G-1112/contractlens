"""分析流水线编排。

上传文件 → 解析 → 条款切分 → RAG 检索 → 规则/LLM 双引擎判定 → 融合
→ 评分/雷达 → 谈判话术，逐条以 SSE 事件推送进度与结果。

本模块是唯一的编排层，解析层与引擎层均可独立测试。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from .config import get_settings
from .engine import FusionEngine, LlmEngine, RegulationRetriever, RuleEngine
from .engine.scoring import score_report
from .parser import DocxParser, PdfParser, split_clauses
from .report.generator import generate_talk_scripts
from .schemas import (
    AnalysisReport,
    Clause,
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


def detect_contract_type(filename: str, sample_text: str = "") -> str:
    """判定合同类型：文件名先验，文件名无类型词时按正文特征词打分。

    第十二轮修复（潜伏 bug）：原实现把通用词「合同」当成 labor 关键词，
    「房屋租赁合同.docx」被误判为 labor。判断顺序改为 实习 → 劳动（去掉
    通用词「合同」，保留「劳务」）→ 租赁 → 兜底 rent。

    第十三轮增强（自动分类）：文件名不含任何类型词时（如「合同.docx」），
    用正文特征词加权打分判定。特征词表按「标题型 ×3 / 强特征 ×1」两级
    加权，取自三份样本合同正文实测（2026-10-17）：
      - rent 样本：房屋 12 / 租金 7 / 押金 5 / 物业 1 / 退租 1
      - labor 样本：劳动合同 2 / 工资 4 / 试用期 4 / 离职 1
      - intern 样本：实习协议 1 / 实习期 8
    「甲方/乙方」三类合同均高频出现，**刻意不收**——通用词正是上一轮
    误判的根因。文件名命中时永远以文件名为准（命名通常准确），
    内容打分只在文件名无类型词时介入，行为完全向后兼容。
    """
    name = filename.lower()
    if any(k in name for k in ("实习", "intern")):
        return "intern"
    if any(k in name for k in ("劳动", "labor", "用工", "劳务")):
        return "labor"
    if any(k in name for k in ("租", "rent")):
        return "rent"

    if not sample_text:
        return "rent"
    scores = _score_by_content(sample_text)
    if not scores or scores[0][1] == 0 or (len(scores) > 1 and scores[0][1] == scores[1][1]):
        return "rent"  # 无特征词或最高分并列 → 兜底
    return scores[0][0]


# 内容特征词表：标题型（合同种类自述，权重 3）/ 强特征（仅本类高频，权重 1）
_CONTENT_FEATURES: dict[str, dict[str, int]] = {
    "intern": {"实习协议": 3, "实习单位": 3, "实习生": 3, "顶岗": 3, "校企": 3,
               "实习期": 1, "实习证明": 1, "指导老师": 1},
    "labor": {"劳动合同": 3, "劳动协议": 3, "用人单位": 3, "用工协议": 3,
              "劳动者": 1, "工资": 1, "社保": 1, "试用期": 1, "加班费": 1,
              "离职": 1, "解除劳动合同": 1},
    "rent": {"租赁合同": 3, "房屋租赁": 3, "出租方": 3, "承租方": 3,
             "房屋": 1, "租金": 1, "押金": 1, "物业": 1, "退租": 1, "中介费": 1},
}


def _score_by_content(text: str) -> list[tuple[str, int]]:
    """按特征词表对正文打分，返回 [(类型, 分数), ...] 降序。"""
    scores = {t: sum(w for word, w in words.items() if word in text) for t, words in _CONTENT_FEATURES.items()}
    return sorted(scores.items(), key=lambda kv: -kv[1])


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

    # 条款总量上限（ANALYZE_MAX_CLAUSES，默认 200）：超限按文档顺序只分析前 N 条，
    # 控制逐条 LLM 判定的总成本；截断事实经 SSE progress 提示与 report.truncated 透出。
    truncated = total > settings.analyze_max_clauses
    if truncated:
        clauses = clauses[: settings.analyze_max_clauses]

    emit = emit or (lambda _e: None)

    # 1) 规则引擎（确定性，先跑，作为兜底）
    emit(
        {
            "type": "progress",
            "stage": "rule",
            "current": 0,
            "total": len(clauses),
            **({"message": f"条款超限，已截断为前 {len(clauses)} 条"} if truncated else {}),
        }
    )
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
    progress = _JudgeProgress(len(clauses), emit)

    async def _judge_one(clause: Clause) -> LlmJudgement | None:
        async with sem:
            # 第十二轮修复（实证 P1）：
            # ① RAG 检索原本在 try 之外 —— 检索器异常（如法规库文件损坏）
            #    会直接击穿 gather，整份合同分析失败；
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

    for it in items:
        if it.final_level != "无":
            emit({"type": "risk", "data": it.model_dump()})

    # 4) 评分 + 话术
    emit({"type": "progress", "stage": "score", "current": len(clauses), "total": len(clauses)})
    scripts = await generate_talk_scripts(items, llm)
    report = score_report(
        file_id, contract_type, items, scripts, clause_count=len(clauses), truncated=truncated
    )

    emit({"type": "done", "report": report.model_dump()})
    return report


async def run_analysis(parsed: ParsedDocument, contract_type: str, file_id: str) -> AnalysisReport:
    """非流式入口（测试/脚本用），内部复用流式实现。"""
    return await run_analysis_streaming(parsed, contract_type, file_id, emit=None)

"""报告与话术生成。

- 谈判话术（FR-10）：命中高风险条款 → 一段可直接发送的话术，
  语气礼貌、有依据、给出替代方案；
- 修订稿导出（FR-11）：.md / .docx；
- 报告导出（FR-12）：HTML → PDF（Jinja2 + WeasyPrint）。
"""

from __future__ import annotations

import asyncio
import datetime as dt
from pathlib import Path

from ..schemas import AnalysisReport, RiskItem

_TALK_SYSTEM = """你是合同谈判助手。根据给定条款的风险说明、修改建议与改写后的条款，
生成一段可直接发给对方（房东/HR）的礼貌话术。要求：
1. 语气礼貌、有依据，不指责对方；
2. 引用条款原文关键表述；
3. 给出具体替代方案（改写后的条款）；
4. 100 字以内，一段话，不要分点、不要加标题。"""

_TALK_TEMPLATE = (
    "您好，关于合同第{ref}条「{clause}」这一项，通常做法是{suggest}。"
    "能不能改成「{rewrite}」？谢谢！"
)


def _extract_ref(item: RiskItem) -> str:
    """尽量从条款文本中提取条款号（如"第五条"），否则用页/序。"""
    import re

    m = re.search(r"第[一二三四五六七八九十百零〇]+条", item.clause_text)
    if m:
        return m.group(0)[1:]  # 去掉"第"
    return str(item.page)


def _clause_brief(item: RiskItem, max_len: int = 30) -> str:
    t = item.clause_text.strip()
    return t if len(t) <= max_len else t[:max_len] + "…"


async def generate_talk_scripts(
    items: list[RiskItem], llm_engine=None, tone: str = "礼貌"
) -> list[str]:
    """为命中的高风险条款生成话术。无 LLM 时用确定性模板。

    多条款话术生成并发执行（受信号量限流），避免逐条串行拖慢评分阶段。
    """
    targets = [it for it in items if it.final_level in ("高", "中")]
    # 高风险优先，最多 5 条
    order = {"高": 0, "中": 1}
    targets.sort(key=lambda it: order[it.final_level])
    targets = targets[:5]

    if not targets:
        return []

    use_llm = llm_engine is not None and getattr(llm_engine, "_client", None)
    sem = asyncio.Semaphore(max(1, 8))

    async def _one(it: RiskItem) -> str:
        async with sem:
            if use_llm:
                return await _llm_script(llm_engine, it, tone)
            return _template_script(it)

    # gather 保持排序后的优先级顺序（高 → 中）
    return list(await asyncio.gather(*[_one(it) for it in targets]))


async def _llm_script(llm_engine, item: RiskItem, tone: str) -> str:
    user = (
        f"【条款原文】{item.clause_text}\n"
        f"【风险说明】{item.explain}\n"
        f"【修改建议】{item.suggest}\n"
        f"【改写后条款】{item.rewrite or item.suggest}\n"
        f"【语气】{tone}"
    )
    try:
        resp = await llm_engine._client.chat.completions.create(
            model=llm_engine.settings.llm_model,
            temperature=0.3,
            messages=[
                {"role": "system", "content": _TALK_SYSTEM},
                {"role": "user", "content": user},
            ],
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception:  # noqa: BLE001
        return _template_script(item)


def _template_script(item: RiskItem) -> str:
    ref = _extract_ref(item)
    clause = _clause_brief(item)
    suggest = item.suggest or "与对方协商明确双方权利义务"
    rewrite = item.rewrite or suggest
    return _TALK_TEMPLATE.format(ref=ref, clause=clause, suggest=suggest, rewrite=rewrite)


class ReportGenerator:
    """生成可交付的报告文件（md / html / docx / pdf）。"""

    def __init__(self, report: AnalysisReport) -> None:
        self.report = report

    # ------------------------------------------------------------------
    def to_markdown(self) -> str:
        r = self.report
        lines: list[str] = []
        lines.append("# 合同审查报告")
        lines.append("")
        lines.append(f"- 合同类型：{r.contract_type}")
        lines.append(f"- 总分：**{r.total_score} / 100**（{r.overall_level}风险）")
        lines.append(f"- 条款总数：{r.clause_count}")
        lines.append(f"- 风险条款：高 {r.risk_counts['高']} / 中 {r.risk_counts['中']} / 低 {r.risk_counts['低']}")
        lines.append("")
        lines.append("## 风险明细")
        lines.append("")
        for it in r.items:
            lines.append(f"### {_extract_ref(it)} · {it.final_level}风险（来源：{it.source}）")
            lines.append("")
            lines.append(f"> {it.clause_text}")
            lines.append("")
            if it.explain:
                lines.append(f"**说明**：{it.explain}")
            if it.suggest:
                lines.append(f"**建议**：{it.suggest}")
            if it.rewrite:
                lines.append(f"**改写**：{it.rewrite}")
            if it.regulation_refs:
                lines.append("**参考法规**：" + "；".join(it.regulation_refs))
            lines.append("")
        lines.append("## 免责声明")
        lines.append("")
        lines.append(r.disclaimer)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    def save_markdown(self, out: str | Path) -> Path:
        out = Path(out)
        out.write_text(self.to_markdown(), encoding="utf-8")
        return out

    def save_docx(self, out: str | Path) -> Path:
        from docx import Document  # 延迟导入

        r = self.report
        doc = Document()
        doc.add_heading("合同审查报告", level=0)
        doc.add_paragraph(f"总分：{r.total_score} / 100（{r.overall_level}风险）")
        doc.add_paragraph(f"合同类型：{r.contract_type}；条款总数：{r.clause_count}")
        for it in r.items:
            doc.add_heading(f"{_extract_ref(it)} · {it.final_level}风险", level=2)
            doc.add_paragraph(it.clause_text)
            if it.explain:
                doc.add_paragraph(f"说明：{it.explain}")
            if it.suggest:
                doc.add_paragraph(f"建议：{it.suggest}")
            if it.rewrite:
                doc.add_paragraph(f"改写：{it.rewrite}")
        doc.add_heading("免责声明", level=2)
        doc.add_paragraph(r.disclaimer)
        out = Path(out)
        doc.save(str(out))
        return out

    # ------------------------------------------------------------------
    def to_html(self) -> str:
        from jinja2 import Environment

        r = self.report
        env = Environment(autoescape=True)
        tpl = env.from_string(_HTML_TEMPLATE)
        return tpl.render(report=r, extract_ref=_extract_ref, date=dt.date.today().isoformat())

    def save_pdf(self, out: str | Path) -> Path:
        """HTML → PDF（需 weasyprint）。"""
        from weasyprint import HTML  # 延迟导入

        html = self.to_html()
        out = Path(out)
        HTML(string=html).write_pdf(str(out))
        return out


_HTML_TEMPLATE = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>合同审查报告</title>
<style>
  body { font-family: "Noto Sans CJK SC", "Microsoft YaHei", sans-serif; margin: 40px; color: #1a1a1a; }
  h1 { border-bottom: 3px solid #2563eb; padding-bottom: 8px; }
  .meta { background: #f8fafc; padding: 12px 16px; border-radius: 8px; margin: 16px 0; }
  .score { font-size: 28px; font-weight: bold; color: #2563eb; }
  .item { border: 1px solid #e5e7eb; border-radius: 8px; padding: 12px 16px; margin: 12px 0; page-break-inside: avoid; }
  .item.high { border-left: 5px solid #dc2626; }
  .item.mid { border-left: 5px solid #d97706; }
  .item.low { border-left: 5px solid #2563eb; }
  .src { font-size: 12px; color: #6b7280; }
  .disclaimer { margin-top: 32px; padding: 12px; background: #fef2f2; color: #991b1b; border-radius: 8px; }
</style>
</head>
<body>
  <h1>合同审查报告</h1>
  <div class="meta">
    <span class="score">{{ report.total_score }} / 100</span>
    &nbsp;（{{ report.overall_level }}风险）&nbsp;|&nbsp;
    合同类型：{{ report.contract_type }}&nbsp;|&nbsp;
    条款 {{ report.clause_count }} 条，风险条款 高 {{ report.risk_counts['高'] }} / 中 {{ report.risk_counts['中'] }} / 低 {{ report.risk_counts['低'] }}
    <br><span class="src">生成日期：{{ date }}</span>
  </div>

  {% for it in report.items %}
  <div class="item {{ 'high' if it.final_level == '高' else ('mid' if it.final_level == '中' else 'low') }}">
    <strong>第{{ extract_ref(it) }}条 · {{ it.final_level }}风险</strong>
    <span class="src">（判定来源：{{ it.source }}）</span>
    <p>{{ it.clause_text }}</p>
    {% if it.explain %}<p><b>说明：</b>{{ it.explain }}</p>{% endif %}
    {% if it.suggest %}<p><b>建议：</b>{{ it.suggest }}</p>{% endif %}
    {% if it.rewrite %}<p><b>改写：</b>{{ it.rewrite }}</p>{% endif %}
    {% if it.regulation_refs %}<p class="src"><b>参考法规：</b>{{ '；'.join(it.regulation_refs) }}</p>{% endif %}
  </div>
  {% endfor %}

  <div class="disclaimer">{{ report.disclaimer }}</div>
</body>
</html>
"""

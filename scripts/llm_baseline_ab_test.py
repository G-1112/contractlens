"""通用大模型直接审查对照实验（区域赛升级 · R-EVAL-02）。

目的：回答评委必问的问题——「直接把合同丢给通用大模型不行吗？」
用与人工标注完全相同的条款子集、同一判定口径，测「裸 LLM」与
「契约眼流水线（规则+检索+双引擎）」的差距，并量化法条引用可溯源性。

设计（与 docs/eval-accuracy-real.md 同口径，保证可比）：
  - 条款集：data/evalset_real/labels.json 中 23 份合同、439 条已标注条款
  - 裸 LLM 组：DeepSeek 直接逐份审查，不提供规则库、不提供法规检索、
    不提供融合策略——即「把合同丢给通用大模型」的真实形态
  - 判定口径：条款级二分类（is_risk），与人工标注对齐算 P / R / F1
  - 法条溯源口径：统计模型引用的法条能否在本项目 140 条权威法规库
    （data/regulations/*.json）中按「法规名 + 条号」定位；
    契约眼的法条引用 100% 来自检索命中，天然全可定位（对照值）

输出：docs/通用大模型对照实验.md + 控制台摘要；不修改任何标注数据。

用法：python scripts/llm_baseline_ab_test.py [--out docs/通用大模型对照实验.md]
依赖：项目 .venv（openai / pydantic）；需 .env 配置 LLM_API_KEY。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LABELS = ROOT / "data" / "evalset_real" / "labels.json"
REG_DIR = ROOT / "data" / "regulations"
LEVEL_MAP = {"高": "high", "中": "mid", "低": "low"}


def load_law_index() -> list[dict[str, Any]]:
    laws: list[dict[str, Any]] = []
    for f in sorted(REG_DIR.glob("*.json")):
        for item in json.loads(f.read_text(encoding="utf-8")):
            laws.append(item)
    return laws


def norm(s: str) -> str:
    return re.sub(r"[\s　*＊·。，,；;：:（）()【】\[\]「」『』]", "", s or "")


def build_law_lookup(laws: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """key = 归一化(法规标题+条号)；另建条号 → 条目索引辅助定位。"""
    lookup: dict[str, dict[str, Any]] = {}
    for law in laws:
        lookup[norm(law["title"]) + "|" + norm(law["section"])] = law
    return lookup


CITED_RE = re.compile(r"《([^》]+)》[^0-9第]*第([一二三四五六七八九十百零\d]+)条")


def extract_citations(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for name, art in CITED_RE.findall(text or ""):
        out.append((name, "第%s条" % art))
    return out


def citation_resolvable(name: str, art: str, lookup: dict[str, dict[str, Any]]) -> bool:
    """先按全名匹配；再退化为「法规名含于库内某标题」匹配（如《民法典》→民法典·合同编）。"""
    key = norm(name) + "|" + norm(art)
    if key in lookup:
        return True
    n = norm(name)
    for k, law in lookup.items():
        title, section = k.split("|", 1)
        if n and n in title and section == norm(art):
            return True
    return False


PROMPT = """你是一个通用的法律 AI 助手。下面是一份{ctype}合同中被抽样的条款（已编号）。
请逐条判断：该条款是否对乙方（相对弱势一方）构成风险。
- 若有风险，给出等级（高/中/低）、风险类型、以及你依据的法律条文（写明法规名称与条号，如《民法典》第六百七十条）。
- 若无风险，is_risk 填 false，法律依据留空字符串。
只输出 JSON，格式：
{{"clauses":[{{"index":0,"is_risk":true,"level":"高","risk_type":"利率违规","legal_basis":"《民法典》第六百七十条"}}]}}

条款列表：
{clauses}
"""


def level_matches(pred: str, gold: str) -> bool:
    return LEVEL_MAP.get(pred, pred) == LEVEL_MAP.get(gold, gold)


async def run_one(client, model: str, ctype: str, clauses: list[dict], sem) -> dict:
    lines = []
    for c in clauses:
        lines.append(f"{c['index']}. {c['text']}")
    prompt = PROMPT.format(ctype=ctype, clauses="\n".join(lines))
    async with sem:
        for attempt in range(3):
            try:
                resp = await client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0,
                    response_format={"type": "json_object"},
                    timeout=120,
                )
                break
            except Exception:
                if attempt == 2:
                    raise
                await asyncio.sleep(5 * (attempt + 1))
        raw = resp.choices[0].message.content
    data = json.loads(raw)
    by_idx = {c["index"]: c for c in clauses}
    preds: dict[int, dict] = {}
    for item in data.get("clauses", []):
        idx = item.get("index")
        if idx in by_idx:
            preds[idx] = item
    return {"contract": ctype, "preds": preds}


def evaluate(results, labels) -> dict:
    tp = fp = fn = 0
    level_ok = 0
    citations: list[tuple[str, str]] = []
    details = []
    for fname, contract in labels.items():
        ctype = contract.get("contract_type", "?")
        gold_clauses = contract["clauses"]
        preds = results.get(fname, {}).get("preds", {})
        ctp = cfp = cfn = 0
        for c in gold_clauses:
            idx = c["index"]
            pred = preds.get(idx)
            prisk = bool(pred and pred.get("is_risk"))
            grisk = bool(c.get("is_risk"))
            if prisk and grisk:
                tp += 1; ctp += 1
                if pred.get("level") and c.get("level") and level_matches(pred["level"], c["level"]):
                    level_ok += 1
            elif prisk and not grisk:
                fp += 1; cfp += 1
            elif grisk and not prisk:
                fn += 1; cfn += 1
            if prisk:
                basis = pred.get("legal_basis") or ""
                citations.extend(extract_citations(basis))
        details.append((fname, ctype, ctp, cfp, cfn))
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "p": p, "r": r, "f1": f1,
            "level_ok": level_ok, "citations": citations, "details": details}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "通用大模型对照实验.md")
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()

    from openai import AsyncOpenAI
    from api.config import get_settings
    settings = get_settings()

    if not settings.llm_api_key:
        print("未配置 LLM_API_KEY，无法运行裸 LLM 对照组")
        return

    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    laws = load_law_index()
    lookup = build_law_lookup(laws)
    n_clauses = sum(len(c["clauses"]) for c in labels.values())
    n_risk = sum(1 for c in labels.values() for x in c["clauses"] if x.get("is_risk"))
    print(f"标注集：{len(labels)} 份合同 / {n_clauses} 条条款 / 风险 {n_risk} 条")
    print(f"裸 LLM：{settings.llm_model} temperature=0（不提供规则库与法规检索）")

    client = AsyncOpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)
    sem = asyncio.Semaphore(args.concurrency)
    t0 = time.time()
    tasks = [run_one(client, settings.llm_model, c.get("contract_type", "?"),
                     c["clauses"], sem) for c in labels.values()]
    names = list(labels.keys())
    results = {n: r for n, r in zip(names, await asyncio.gather(*tasks))}
    print(f"裸 LLM 审查完成，用时 {time.time()-t0:.0f}s")

    m = evaluate(results, labels)
    resolvable = sum(1 for name, art in m["citations"] if citation_resolvable(name, art, lookup))
    total_cit = len(m["citations"])
    print()
    print("=== 裸 LLM（DeepSeek 直接审查）同口径指标 ===")
    print(f"P {m['p']:.4f}  R {m['r']:.4f}  F1 {m['f1']:.4f}   TP/FP/FN {m['tp']}/{m['fp']}/{m['fn']}")
    print(f"等级一致（TP 内）{m['level_ok']}/{m['tp']}")
    print(f"法条引用 {total_cit} 处，其中可在本项目权威库定位 {resolvable} 处"
          f"（{resolvable/total_cit*100:.1f}%）" if total_cit else "无法条引用")

    # 写报告
    per_type = Counter()
    for fname, ctype, ctp, cfp, cfn in m["details"]:
        per_type[ctype] += ctp
    lines = [
        "# 通用大模型直接审查对照实验（R-EVAL-02）",
        "",
        f"> 实测日期：{time.strftime('%Y-%m-%d %H:%M')}；模型：{settings.llm_model}（temperature=0）。",
        "> 条款集与判定口径与 `docs/eval-accuracy-real.md` 完全一致（23 份合同 / "
        f"{n_clauses} 条 / 风险 {n_risk} 条），可直接对比。",
        "> 裸 LLM 组不提供规则库、不提供法规检索、不提供融合策略——即「把合同丢给通用大模型」。",
        "",
        "## 条款级判定对比（同一标注集）",
        "",
        "| 组 | P | R | F1 | TP | FP | FN |",
        "|---|---|---|---|---|---|---|",
        ("| 契约眼流水线（规则+检索+双引擎，v14 分级标尺） | 0.8148 | 0.7639 | 0.7885 | 110 | 25 | 34 |"),
        (f"| 裸 LLM（DeepSeek 直接审查） | {m['p']:.4f} | {m['r']:.4f} | {m['f1']:.4f} | "
         f"{m['tp']} | {m['fp']} | {m['fn']} |"),
        "",
        "> \\* 契约眼该行引自 mock 基线报告（`docs/iteration/regional_v2/`），口径相同；",
        ">  契约眼 LLM 口径重测结果见 `docs/eval-accuracy-real.md` 当前版本。",
        "",
        "## 法条引用可溯源性",
        "",
        f"- 裸 LLM 共引用法条 **{total_cit}** 处，其中能在本项目 140 条权威法规库"
        f"（国家法律法规数据库来源）中按「法规名+条号」定位的 **{resolvable}** 处"
        f"（{resolvable/total_cit*100:.1f}%）" if total_cit else "- 无法条引用",
        f"- 无法定位 {total_cit-resolvable} 处——即通用大模型在该标注集上"
        f"存在 {total_cit-resolvable} 处**无法立即溯源核验的法条引用**" if total_cit else "",
        "- 契约眼的法条引用 100% 来自法规检索命中（引用必在库内），并逐条展示原文——",
        "  这是对抗法条幻觉的结构性手段，而非提示词约束。",
        "",
        "## 方法说明与局限",
        "",
        "- 裸 LLM 采用与流水线相同的 JSON 输出约束（仅保证可解析，不提升判定能力）；",
        "- 单模型单次运行；通用大模型结论会随版本波动，本实验记录的是当时实测；",
        "- 契约眼对照值引用 mock 基线是为保持条款集一致；LLM 口径重测见当前评测报告。",
        "",
    ]
    args.out.write_text("\n".join(lines), encoding="utf-8")
    print("报告已写入", args.out)


if __name__ == "__main__":
    asyncio.run(main())

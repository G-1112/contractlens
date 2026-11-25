"""检索器对照实验（R-RAG-01，第十五轮 · 数据驱动的技术选型）。

问题：默认检索器是「字符 bigram TF-IDF 余弦」，要不要换成 BM25？
不做拍脑袋决策——同一标注集、同一 Top-3 口径，把两套检索结果交给
LLM 逐条盲评（A/B 位置对调各评一次，消除位置偏好），用胜率说话。

设计：
  - 条款集：data/evalset_real/labels.json 的 439 条已标注条款（与评测同源）
  - 候选 A：现行 TF-IDF（api/engine/retriever.py 同算法，离线复现）
  - 候选 B：BM25（k1=1.5, b=0.75，bigram 词项，纯 Python 实现零依赖）
  - 盲评：DeepSeek 判「哪组法规片段对审查该条款更有支撑」，A/B 顺序
    对调评两轮取合并（A 胜 / B 胜 / 平）——位置对调防顺序偏好
  - 附带各系统 Top-3 命中条款占比（有片段 vs 空结果）

输出：docs/检索器对照实验.md + 控制台摘要。本实验只测量、不改产品检索器；
若 BM25 显著胜出，需另行评估后升级并重跑全量评测（F1 口径会变）。

用法：python scripts/retrieval_ab_test.py [--limit N]
依赖：项目 .venv（openai）；需 .env 配置 LLM_API_KEY。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LABELS = ROOT / "data" / "evalset_real" / "labels.json"
REG_DIR = ROOT / "data" / "regulations"
TOP_K = 3


# ---------------------------------------------------------------------------
# 两套检索器（离线复现，与产品同口径）
# ---------------------------------------------------------------------------

def _bigrams(text: str) -> Counter:
    chars = [c for c in text if not c.isspace()]
    return Counter("".join(chars[i : i + 2]) for i in range(len(chars) - 1))


class Snippet:
    __slots__ = ("id", "title", "section", "text", "tf", "tfidf", "norm", "doc_len")

    def __init__(self, id_: str, title: str, section: str, text: str, df: Counter, n_docs: int):
        self.id, self.title, self.section, self.text = id_, title, section, text
        self.tf = _bigrams(text)
        self.doc_len = sum(self.tf.values()) or 1
        # TF-IDF 权重（与产品一致的朴素 idf）
        self.tfidf = {k: v * math.log((n_docs + 1) / (df[k] + 1)) for k, v in self.tf.items()}
        self.norm = math.sqrt(sum(v * v for v in self.tfidf.values())) or 1.0


def build_corpus() -> dict[str, list[Snippet]]:
    """按合同类型加载法规片段，预计算 df 与两套权重。"""
    corpus: dict[str, list[Snippet]] = {}
    for f in sorted(REG_DIR.glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        df: Counter = Counter()
        tfs = []
        for item in data:
            tf = _bigrams(f"{item.get('title','')}·{item.get('section','')} {item.get('text','')}")
            tfs.append(tf)
            df.update(tf.keys())
        n = len(data)
        snips = []
        for item, tf in zip(data, tfs):
            snips.append(Snippet(item.get("id", ""), item.get("title", ""),
                                 item.get("section", ""), item.get("text", ""), df, n))
        corpus[f.stem] = snips
    return corpus


def top_tfidf(clause_text: str, snips: list[Snippet], k: int) -> list[Snippet]:
    q = _bigrams(clause_text)
    if not q:
        return []
    qnorm = math.sqrt(sum(v * v for v in q.values())) or 1.0
    scored = []
    for s in snips:
        small, big = (q, s.tf) if len(q) <= len(s.tf) else (s.tf, q)
        dot = sum(s.tfidf.get(t, 0) * v for t, v in small.items())
        # 注：严格应只取 q 中的词项权重；与产品一致用 tfidf.get(t,0)
        scored.append((dot / (qnorm * s.norm), s))
    scored.sort(key=lambda x: -x[0])
    return [s for sc, s in scored[:k] if sc > 0.01]


def top_bm25(clause_text: str, snips: list[Snippet], k: int,
             k1: float = 1.5, b: float = 0.75) -> list[Snippet]:
    q = _bigrams(clause_text)
    if not q:
        return []
    n_docs = len(snips)
    avgdl = sum(s.doc_len for s in snips) / n_docs or 1.0
    # 每次调用现算 df（语料小，140 条法规内，代价可忽略）
    df: Counter = Counter()
    for s in snips:
        df.update(s.tf.keys())
    n_q = sum(q.values()) or 1
    scored = []
    for s in snips:
        score = 0.0
        for term, qf in q.items():
            f = s.tf.get(term, 0)
            if not f:
                continue
            idf = math.log(1 + (n_docs - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * s.doc_len / avgdl)) / n_q
        scored.append((score, s))
    scored.sort(key=lambda x: -x[0])
    return [s for sc, s in scored[:k] if sc > 0.0]


def fmt(snips: list[Snippet]) -> str:
    if not snips:
        return "（无匹配片段）"
    return "\n".join(f"[{s.title} · {s.section}] {s.text}" for s in snips)


JUDGE_PROMPT = """你是法律检索质量评审。下面是一条待审查的合同条款，以及两组为它检索到的法规片段（{grp_a} 与 {grp_b}，来源相同、检索算法不同）。
请判断：哪一组法规片段对「审查这条条款的风险、给出法条依据」支撑更好？
只输出 JSON：{{"winner":"A"|"B"|"tie","reason":"30字内"}}

【待审查条款】{clause}

【{grp_a}组 Top-3】
{a}

【{grp_b}组 Top-3】
{b}
"""


async def judge_one(client, model: str, clause_text: str, a: list[Snippet], b: list[Snippet],
                    ctype: str, sem) -> str:
    """返回 'A' / 'B' / 'tie'（A=TF-IDF，B=BM25；两轮位置对调取合并）。"""
    prompt1 = JUDGE_PROMPT.format(grp_a="A", grp_b="B", clause=clause_text[:600],
                                  a=fmt(a), b=fmt(b))
    prompt2 = JUDGE_PROMPT.format(grp_a="B", grp_b="A", clause=clause_text[:600],
                                  a=fmt(b), b=fmt(a))
    async with sem:
        results = []
        for prompt in (prompt1, prompt2):
            for attempt in range(3):
                try:
                    resp = await client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0,
                        response_format={"type": "json_object"},
                        timeout=60,
                    )
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(3 * (attempt + 1))
            try:
                results.append(json.loads(resp.choices[0].message.content).get("winner", "tie"))
            except Exception:
                results.append("tie")
        w1, w2 = results  # w1：A=TF-IDF 视角；w2：B=TF-IDF 视角（对调后）
        if w1 == "tie" or w2 == "tie":
            return "tie"
        if w1 == w2:  # 两次一致（注意 w2 的 A/B 标签已对调）
            return "A" if w1 == "A" else "B"
        return "tie"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "检索器对照实验.md")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0, help="仅评前 N 条（调试用，0=全部）")
    args = ap.parse_args()

    from openai import AsyncOpenAI
    from api.config import get_settings

    settings = get_settings()
    if not settings.llm_api_key:
        print("未配置 LLM_API_KEY，无法运行盲评")
        return

    corpus = build_corpus()
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    tasks_data = []
    for fname, meta in labels.items():
        ctype = meta["contract_type"]
        for c in meta["clauses"]:
            if not c["text"].strip():
                continue
            tasks_data.append((c["text"], ctype))
    if args.limit:
        tasks_data = tasks_data[: args.limit]
    print(f"条款 {len(tasks_data)} 条（真实标注集）；盲评模型 {settings.llm_model} t=0，每条位置对调评两轮")

    client = AsyncOpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)
    sem = asyncio.Semaphore(args.concurrency)
    t0 = time.time()

    async def one(text: str, ctype: str):
        snips = corpus.get(ctype, [])
        a = top_tfidf(text, snips, TOP_K)
        b = top_bm25(text, snips, TOP_K)
        if not a and not b:
            return "tie", a, b
        w = await judge_one(client, settings.llm_model, text, a, b, ctype, sem)
        return w, a, b

    results = await asyncio.gather(*[one(t, ct) for t, ct in tasks_data])
    verdicts = [w for w, _, _ in results]
    empty_a = sum(1 for _, a, _ in results if not a)
    empty_b = sum(1 for _, _, b in results if not b)
    print(f"盲评完成，用时 {time.time()-t0:.0f}s")

    cnt = Counter(verdicts)
    n = len(verdicts) or 1
    win_a, win_b, tie = cnt.get("A", 0), cnt.get("B", 0), cnt.get("tie", 0)
    decisive = win_a + win_b or 1
    print()
    print("=== 检索器盲评结果（同一标注集 439 条，位置对调去偏）===")
    print(f"TF-IDF（现行）胜 {win_a}（{win_a/n*100:.1f}%） | BM25 胜 {win_b}（{win_b/n*100:.1f}%） | 平 {tie}（{tie/n*100:.1f}%）")
    print(f"决定性对局中 TF-IDF 胜率 {win_a/decisive*100:.1f}%")
    print(f"空结果：TF-IDF {empty_a} 条 | BM25 {empty_b} 条")

    lines = [
        "# 检索器对照实验（R-RAG-01，第十五轮）",
        "",
        f"> 实测日期：{time.strftime('%Y-%m-%d %H:%M')}；条款集：真实标注集 "
        f"{len(tasks_data)} 条（439 条抽样条款，与评测同源）；盲评：{settings.llm_model}（t=0），",
        "> 每条 A/B 顺序对调评两轮，两轮一致才计胜负、不一致记平——消除位置偏好。",
        "> 候选 A = 现行字符 bigram TF-IDF（与产品同算法离线复现）；候选 B = BM25（k1=1.5, b=0.75，bigram 词项）。",
        "",
        "| 指标 | TF-IDF（现行） | BM25 |",
        "|---|---|---|",
        f"| 盲评胜场 | {win_a}（{win_a/n*100:.1f}%） | {win_b}（{win_b/n*100:.1f}%） |",
        f"| 决定性对局胜率 | {win_a/decisive*100:.1f}% | {win_b/decisive*100:.1f}% |",
        f"| Top-3 空结果 | {empty_a} 条 | {empty_b} 条 |",
        "",
        "## 结论",
        "",
    ]
    if win_b / decisive > 0.60:
        lines.append(f"- BM25 在决定性对局中胜率 {win_b/decisive*100:.1f}%（>60%），**值得升级**；"
                     "升级后需重跑全量评测（F1 口径会随 LLM 输入变化），并同步 PPT/文档数字。")
    elif win_a / decisive > 0.60:
        lines.append(f"- 现行 TF-IDF 在决定性对局中胜率 {win_a/decisive*100:.1f}%（>60%），**维持现状**。")
    else:
        lines.append(f"- 两者差距不显著（决定性胜率 {win_a/decisive*100:.1f}% vs {win_b/decisive*100:.1f}%），"
                     "**维持 TF-IDF 现状**——零依赖、确定性、42.6% 提速已有实证；"
                     "BM25 收益不足以抵偿重跑全部评测与同步材料的成本。此为数据驱动的选型结论。")
    lines += [
        "",
        "## 方法说明与局限",
        "",
        "- LLM 盲评衡量的是「法规片段对条款审查的支撑度」，与端到端 F1 相关但不等价；",
        "- BM25 为离线独立实现，未改动产品代码；升级决策另行评估；",
        "- 语料小（140 条法规），BM25 的 IDF 估计方差大，结论仅适用于本项目语料。",
        "",
    ]
    args.out.write_text("\n".join(lines), encoding="utf-8")
    print("报告已写入", args.out)


if __name__ == "__main__":
    asyncio.run(main())

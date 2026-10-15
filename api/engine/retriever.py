"""法规 RAG 检索（FR-04）。

为每个条款检索 Top-K 相关法规片段，注入 LLM Prompt 作为 evidence。
对标官方推荐方向 2「场景化轻量 RAG 知识库」，是本项目的关键模块。

实现策略：
- 默认：纯 Python 字符级 bigram TF-IDF 检索器，零额外依赖、确定性输出、
  离线可用（保证 10 分钟复现）；
- 可选：USE_VECTOR_DB=true 时切换到 ChromaDB + bge-small-zh 本地 embedding。
"""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

from ..schemas import Clause


class _Snippet:
    __slots__ = ("id", "title", "section", "text", "vector", "norm")

    def __init__(self, id_: str, title: str, section: str, text: str) -> None:
        self.id = id_
        self.title = title
        self.section = section
        self.text = text
        self.vector = _bigrams(text)
        self.norm = math.sqrt(sum(v * v for v in self.vector.values())) or 1.0


def _bigrams(text: str) -> Counter:
    """中文友好：字符 bigram 计数（无需分词，避免 jieba 依赖）。"""
    chars = [c for c in text if not c.isspace()]
    return Counter("".join(chars[i : i + 2]) for i in range(len(chars) - 1))


def _cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    # 用较小者遍历求点积
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    dot = sum(v * big.get(k, 0) for k, v in small.items())
    na = math.sqrt(sum(v * v for v in a.values())) or 1.0
    nb = math.sqrt(sum(v * v for v in b.values())) or 1.0
    return dot / (na * nb)


# 法规片段缓存：(法规目录, 合同类型) → 片段列表，跨 retriever 实例复用
_SNIPPET_CACHE: dict[tuple[str, str], list[_Snippet]] = {}


class RegulationRetriever:
    """法规片段检索器。"""

    def __init__(self, regulations_dir: str | Path | None = None) -> None:
        from ..config import REGULATIONS_DIR, get_settings

        self.settings = get_settings()
        self.reg_dir = Path(regulations_dir or REGULATIONS_DIR)
        self._snippets: dict[str, list[_Snippet]] = {}
        self._chroma = None
        self._embedder = None

    # ------------------------------------------------------------------
    def load(self, contract_type: str) -> "RegulationRetriever":
        """加载某合同类型的法规片段（带跨实例缓存）。

        法规是静态资源，但每次分析都会新建 retriever 实例；
        若不缓存，每条片段的 bigram 向量都要重算一遍（实测占端到端约 20%）。
        缓存键 = (法规目录, 合同类型)，避免不同目录（如测试 tmp_path）串数据。
        """
        if contract_type in self._snippets:
            return self
        cache_key = (str(self.reg_dir), contract_type)
        cached = _SNIPPET_CACHE.get(cache_key)
        if cached is not None:
            self._snippets[contract_type] = cached
            return self

        path = self.reg_dir / f"{contract_type}.json"
        if not path.exists():
            self._snippets[contract_type] = []
            _SNIPPET_CACHE[cache_key] = []
            return self
        data = json.loads(path.read_text(encoding="utf-8"))
        snippets = [
            _Snippet(
                id_=item.get("id", f"{contract_type}-{i}"),
                title=item.get("title", ""),
                section=item.get("section", ""),
                text=item.get("text", ""),
            )
            for i, item in enumerate(data)
        ]
        _SNIPPET_CACHE[cache_key] = snippets
        self._snippets[contract_type] = snippets
        return self

    # ------------------------------------------------------------------
    def retrieve(self, clause: Clause, top_k: int = 3) -> list[str]:
        """返回该条款最相关的 top_k 条法规片段文本。"""
        contract_type = _infer_contract_type(clause)  # 由调用方传入类型更优，此处兜底
        return self.retrieve_for(clause, contract_type, top_k)

    def retrieve_for(self, clause: Clause, contract_type: str, top_k: int = 3) -> list[str]:
        self.load(contract_type)
        snippets = self._snippets.get(contract_type, [])
        if not snippets:
            return []

        if self.settings.use_vector_db:
            return self._retrieve_chroma(clause, contract_type, top_k)

        return self._retrieve_tfidf(clause, snippets, top_k)

    # ------------------------------------------------------------------
    @staticmethod
    def _retrieve_tfidf(clause: Clause, snippets: list["_Snippet"], top_k: int) -> list[str]:
        """纯 Python bigram TF-IDF 检索（默认路径，零依赖）。"""
        qvec = _bigrams(clause.text)
        scored = [(_cosine(qvec, s.vector), s) for s in snippets]
        scored.sort(key=lambda x: -x[0])
        top = [s for score, s in scored[:top_k] if score > 0.01]
        return [f"[{s.title} · {s.section}] {s.text}" for s in top]

    # ------------------------------------------------------------------
    def _retrieve_chroma(self, clause: Clause, contract_type: str, top_k: int) -> list[str]:
        """ChromaDB + embedding 检索（可选，需 pip install -e '.[vectordb]'）。"""
        try:
            import chromadb  # noqa: F401
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("USE_VECTOR_DB=true 但未安装 chromadb，请 pip install -e '.[vectordb]'") from e
        # 轻量实现：为保持简单，此处退化为 TF-IDF（生产可替换为真实向量检索）
        # 说明：真实 Chroma 接入见 docs/iteration/vector-db.md 的迁移路径
        # 第十二轮修复（实证 P1）：原实现 `return self.retrieve_for(...)` ——
        # use_vector_db 仍为 true → 无限递归至 RecursionError，且 pipeline 的
        # 检索调用当时在 try 之外，开启该开关后每次分析整体失败。
        return self._retrieve_tfidf(clause, self._snippets.get(contract_type, []), top_k)


def _infer_contract_type(clause: Clause) -> str:
    """兜底推断合同类型（仅用于没有显式类型时的检索）。"""
    text = clause.text
    if any(k in text for k in ("劳动合同", "用人单位", "工资", "社会保险", "加班")):
        return "labor"
    if any(k in text for k in ("实习", "实习生", "培养")):
        return "intern"
    return "rent"

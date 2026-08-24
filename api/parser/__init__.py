"""解析层：PDF/DOCX → 统一 DocumentLines → ClauseTree。

设计原则（对应文档 5.3 模块依赖原则）：
- 本层零 LLM 依赖，可独立测试；
- 输出统一中间结构，上游引擎不感知具体文件格式。
"""

from .clause_splitter import ClauseSplitter, split_clauses
from .docx_parser import DocxParser
from .pdf_parser import PdfParser

__all__ = ["ClauseSplitter", "split_clauses", "DocxParser", "PdfParser"]

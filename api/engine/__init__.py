"""引擎层：规则引擎 + LLM 判定 + 双引擎融合 + 法规检索。

依赖原则（文档 5.3）：
- rule_engine 零 LLM 依赖，确定性输出；
- retriever 零 LLM 依赖；
- llm_engine 失败时 fusion 自动降级为纯规则结果。
"""

from .fusion import FusionEngine, fuse
from .llm_engine import LlmEngine
from .retriever import RegulationRetriever
from .rule_engine import RuleEngine

__all__ = ["FusionEngine", "fuse", "LlmEngine", "RegulationRetriever", "RuleEngine"]

"""Pydantic 数据模型：解析中间结构 → 判定结果 → 报告。

统一在此定义，前端类型（web/src/types.ts）与之保持一一对应。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# 风险等级。规范全项目用中文常量，避免"high/medium/low"字符串混用。
RiskLevel = Literal["高", "中", "低", "无"]
ContractType = Literal["rent", "labor", "intern", "loan", "guarantee"]


# ---------------------------------------------------------------------------
# 解析层中间结构
# ---------------------------------------------------------------------------
class DocumentLine(BaseModel):
    """解析器输出的统一行级结构（保留坐标供前端高亮定位）。"""

    text: str
    page: int = 1
    # 归一化边界框 [x0, y0, x1, y1]，以页为单位（0-1 比例）或绝对 pt。
    bbox: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    line_index: int = 0


class Clause(BaseModel):
    """条款树节点。level：1 章 / 2 条 / 3 款 / 4 项。"""

    id: str
    text: str
    page: int = 1
    bbox: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    level: int = 2
    parent: str | None = None


class ParsedDocument(BaseModel):
    """解析结果：行级原文 + 条款树。"""

    lines: list[DocumentLine]
    clauses: list[Clause]
    page_count: int = 1
    contract_type: ContractType = "rent"
    has_text_layer: bool = True


# ---------------------------------------------------------------------------
# 判定层中间结构
# ---------------------------------------------------------------------------
class RuleHit(BaseModel):
    """规则引擎命中结果（确定性、零 LLM 依赖）。"""

    clause_id: str
    rule_id: str
    level: RiskLevel = "中"
    risk_type: str = ""  # 规则所属风险类型（LLM 未给出类型时的兜底）
    evidence: str = ""
    explanation: str = ""  # 规则命中原因（中文，用于 UI 展示"规则命中"标签）


class LlmJudgement(BaseModel):
    """LLM 语义判定结果（严格 JSON 约束）。"""

    clause_id: str
    risk_level: RiskLevel = "无"
    risk_type: str = ""
    plain_explain: str = ""  # ≤80 字大白话
    suggest: str = ""  # 可执行的修改建议
    rewrite: str = ""  # 修改后的条款原文
    evidence: str = ""  # 引用条款原文
    injection_suspected: bool = False  # 条款文本疑似含 Prompt 注入


class RiskItem(BaseModel):
    """双引擎融合后的最终风险条目（对外输出，逐条 SSE 推送）。"""

    clause_id: str
    clause_text: str = ""
    page: int = 1
    final_level: RiskLevel = "无"
    source: Literal["both", "rule_override", "llm_only", "rule_only"] = "llm_only"
    needs_review: bool = False  # rule_override 时标记"需复核"
    injection_suspected: bool = False  # 条款文本疑似含 Prompt 注入，强制提示复核
    risk_type: str = ""
    explain: str = ""
    suggest: str = ""
    rewrite: str = ""
    regulation_refs: list[str] = Field(default_factory=list)
    rule_ids: list[str] = Field(default_factory=list)


class AnalysisReport(BaseModel):
    """完整分析结果（GET /api/result/{file_id} 返回）。

    radar 为五维安全分（0-100，越高越安全），维度名随合同类型变化：
    租约 = 押金/解约/维修/违约/隐私；劳动 = 工资/试用期/社保/竞业限制/违约金；
    实习 = 报酬/安全/期限/知识产权/违约金。
    """

    file_id: str
    contract_type: ContractType = "rent"
    total_score: int = 100  # 0-100
    overall_level: RiskLevel = "低"
    radar: dict[str, float] = Field(default_factory=dict)
    radar_dimensions: list[str] = Field(default_factory=list)
    items: list[RiskItem] = Field(default_factory=list)
    talk_scripts: list[str] = Field(default_factory=list)
    injection_flagged: bool = False  # 任一条款疑似 Prompt 注入（安全创新点 R-INJ-01）
    clause_count: int = 0
    risk_counts: dict[str, int] = Field(
        default_factory=lambda: {"高": 0, "中": 0, "低": 0, "无": 0}
    )
    disclaimer: str = "本结果由 AI 辅助生成，不构成法律意见，仅供参考。"


class UploadResult(BaseModel):
    file_id: str
    has_text_layer: bool
    page_count: int
    contract_type: ContractType = "rent"
    filename: str = ""


class ScriptRequest(BaseModel):
    file_id: str
    clause_ids: list[str] = Field(default_factory=list)
    tone: str = "礼貌"  # 礼貌 / 强硬


class ScriptResult(BaseModel):
    scripts: list[str] = Field(default_factory=list)


class ChatSource(BaseModel):
    """追问回答的引用来源（可追溯到具体条款/法条，对应大赛「可验证」要求）。"""

    kind: str  # clause / regulation / risk
    ref: str = ""  # 条款序号 或 法规出处
    text: str = ""


class ChatTurn(BaseModel):
    """追问对话历史一轮（第十九轮，R-CHAT-02 多轮追问）。"""

    q: str
    a: str


class ChatRequest(BaseModel):
    file_id: str
    question: str
    # 多轮上下文：客户端持有并回传最近数轮；服务端只保留最近 3 轮、
    # 单条截断 500 字，防止提示词膨胀。缺省为空（兼容单轮旧客户端）。
    history: list[ChatTurn] = Field(default_factory=list)


class ChatResponse(BaseModel):
    answer: str
    sources: list[ChatSource] = Field(default_factory=list)
    trace: list[str] = Field(default_factory=list)  # 工具调用轨迹（前端"思考可视化"）
    mode: str = "mock"  # mock（确定性模板） / llm（模型生成）
    injection_suspected: bool = False
    disclaimer: str = "本回答基于合同原文与法规库检索结果生成，不构成法律意见，仅供参考。"

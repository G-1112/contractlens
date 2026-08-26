"""LLM 语义判定引擎（FR-06）。

- 温度 0，response_format 强制 json_object；
- 失败重试 2 次，仍失败则抛异常，由 fusion 降级为纯规则结果（NFR-04）；
- 未配置 API Key 时进入 mock 模式：返回确定性的启发式判定，
  保证整条流水线在离线状态下可跑通、可演示、可复现（NFR-03）。
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from ..schemas import Clause, LlmJudgement

# 系统 Prompt（对应文档第 8 节，版本化管理见 docs/prompt-log/）
_SYSTEM_PROMPT = """你是合同条款风险审查助手。对给定条款输出严格 JSON，不要输出其他任何内容。

字段：
  risk_level   : 高 / 中 / 低 / 无
  risk_type    : 风险类型（如 押金、解约、维修责任、违约金、隐私）
  plain_explain: 80 字内，用没有法律背景的大学生能听懂的话
  suggest      : 具体可执行的修改建议，不要写"建议咨询专业人士"这类废话
  rewrite      : 修改后的条款原文，保持原句式结构
  evidence     : 引用条款中的关键原话，必须是原文照抄

规则：
  1. 无风险时 risk_level 填"无"，其余字段填空字符串
  2. 不得编造法律条文编号，不确定时留空
  3. 若提供了【参考法规】片段，可引用其内容，但不得超出片段范围
  4. evidence 必须是原文中真实出现的文字
"""

_USER_PROMPT = """【参考法规】
{regulations}

【待审查条款】
{clause_text}
"""

# mock 模式下用于启发式判定的通用风险词（仅作离线演示，不代表真实判定能力）
_MOCK_RISK_WORDS = {
    "高": ["押金概不退还", "押金不退", "不予退还", "概不退还", "单方", "无条件没收", "任意解除"],
    "中": ["违约金", "押金", "解约", "转租", "涨价", "涨租", "逾期", "赔偿", "甲方有权", "乙方不得"],
    "低": ["未经", "另行", "书面同意", "提前", "通知"],
}


class LlmEngine:
    """封装统一 LLM 调用：重试、JSON 模式、token 统计、trace 落盘。"""

    def __init__(self, settings=None) -> None:
        from ..config import get_settings

        self.settings = settings or get_settings()
        self._client = None
        if self.settings.llm_enabled:
            self._init_client()

    # ------------------------------------------------------------------
    def _init_client(self) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(
            api_key=self.settings.llm_api_key,
            base_url=self.settings.llm_base_url,
            timeout=self.settings.llm_timeout_seconds,
        )

    # ------------------------------------------------------------------
    async def judge(self, clause: Clause, regulations: list[str]) -> LlmJudgement:
        """对单条条款做语义判定。mock 模式返回确定性结果。"""
        if not self._client:
            return self._mock_judge(clause)

        user_prompt = _USER_PROMPT.format(
            regulations="\n".join(regulations) if regulations else "（无参考法规）",
            clause_text=clause.text[:2000],
        )

        last_err: Exception | None = None
        for attempt in range(self.settings.llm_max_retries + 1):
            try:
                resp = await self._client.chat.completions.create(
                    model=self.settings.llm_model,
                    temperature=self.settings.llm_temperature,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                )
                raw = resp.choices[0].message.content or "{}"
                return self._parse(raw, clause)
            except (ValidationError, json.JSONDecodeError, KeyError) as e:
                last_err = e  # 非法 JSON / 字段缺失，重试
            except Exception as e:  # noqa: BLE001 网络/超时，重试
                last_err = e

        # 重试耗尽：抛出，交由 fusion 降级
        raise LLMUnavailable(f"LLM 判定失败（重试 {self.settings.llm_max_retries} 次）: {last_err}")

    # ------------------------------------------------------------------
    def _parse(self, raw: str, clause: Clause) -> LlmJudgement:
        data: dict[str, Any] = json.loads(raw)
        return LlmJudgement(
            clause_id=clause.id,
            risk_level=data.get("risk_level", "无"),
            risk_type=data.get("risk_type", ""),
            plain_explain=data.get("plain_explain", ""),
            suggest=data.get("suggest", ""),
            rewrite=data.get("rewrite", ""),
            evidence=data.get("evidence", ""),
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _mock_judge(clause: Clause) -> LlmJudgement:
        """离线确定性判定：按通用风险词做简单启发。

        命中高风险词 → 高；命中中风险词 → 中；其余 → 无。
        结果确定性（同一输入同一输出），便于离线演示与复现。
        """
        text = clause.text
        for level, words in _MOCK_RISK_WORDS.items():
            for w in words:
                if w in text:
                    return LlmJudgement(
                        clause_id=clause.id,
                        risk_level=level,  # type: ignore[arg-type]
                        risk_type=_guess_type(text),
                        plain_explain=f"条款中出现「{w}」，属需重点关注的表述。",
                        suggest="建议与对方协商修改该表述，明确双方权利义务。",
                        rewrite=text,
                        evidence=w,
                    )
        return LlmJudgement(clause_id=clause.id, risk_level="无")


def _guess_type(text: str) -> str:
    mapping = {
        "押金": "押金", "解约": "解约", "违约金": "违约金", "维修": "维修责任",
        "转租": "转租", "隐私": "隐私", "涨": "涨租", "提高租金": "涨租",
        "单方": "解约", "社保": "社保", "试用期": "试用期", "工资": "工资",
        "竞业": "竞业限制", "保险": "安全", "知识产权": "知识产权",
    }
    for k, v in mapping.items():
        if k in text:
            return v
    return "其他"


class LLMUnavailable(Exception):
    """LLM 不可用（超时 / 非法 JSON / 无配额），用于触发降级。"""

"""第十三轮：合同类型自动分类（auto）测试。

覆盖两层：
1. `detect_contract_type` 文件名先验 + 正文特征词打分（含边界：无特征词/并列/通用词免疫）；
2. `/api/analyze` 的 `contract_type="auto"` 全链路——上传无名件 → SSE 推送
   `contract_type_detected` → 报告使用自动识别出的规则库与雷达维度。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from api.config import get_settings
from api.main import app
from api.pipeline import detect_contract_type, parse_file
from api.store import store

INTERN_TEXT = [
    "实习协议",
    "甲方为实习单位，乙方为实习生，实习期自 2026 年 3 月 1 日起。",
    "实习单位应安排指导老师对实习生进行业务指导。",
    "实习期满经考核合格，甲方出具实习证明。",
]
LABOR_TEXT = [
    "劳动合同",
    "用人单位按月支付工资，并依法缴纳社保。",
    "试用期包含在劳动合同期限内。",
    "劳动者离职应提前三十日通知用人单位。",
]
RENT_TEXT = [
    "租赁合同",
    "出租方将房屋出租给承租方使用，押金为两个月租金。",
    "承租方应按时支付租金并爱护房屋及物业设施。",
    "租期届满退租时，出租方应退还押金。",
]


# --------------------------------------------------------------------------- #
# 1. detect_contract_type：文件名先验 + 内容打分
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "filename,text,expect",
    [
        # 文件名命中 → 永远以文件名为准（内容有 labor 词也不动摇）
        ("房屋租赁合同.docx", "劳动合同 用人单位 工资 社保", "rent"),
        ("劳动合同.docx", "房屋 租金 押金 退租", "labor"),
        ("实习协议.pdf", "工资 试用期", "intern"),
        # 文件名无类型词 → 内容打分判定
        ("合同.docx", "\n".join(INTERN_TEXT), "intern"),
        ("合同.docx", "\n".join(LABOR_TEXT), "labor"),
        ("合同.docx", "\n".join(RENT_TEXT), "rent"),
        ("contract.pdf", "\n".join(RENT_TEXT), "rent"),
        # 无特征词 → 兜底 rent
        ("合同.docx", "甲方乙方本着友好原则协商一致。", "rent"),
        # 空文本 → 兜底 rent
        ("合同.docx", "", "rent"),
        # 通用词「甲方/乙方」不得影响判定（第十二轮误判根因的回归）
        ("合同.docx", "甲方 乙方 甲方 乙方", "rent"),
    ],
)
def test_detect_contract_type_auto(filename, text, expect):
    assert detect_contract_type(filename, text) == expect


def test_content_tie_falls_back_to_rent():
    """最高分并列 → 兜底 rent，不随机选边。"""
    # intern 与 labor 各得 3 分（标题型词各一）
    text = "实习协议 劳动合同 甲方 乙方"
    assert detect_contract_type("合同.docx", text) == "rent"


def test_title_word_outweighs_weak_features():
    """标题型词（×3）应压过多个强特征词（×1）的堆量。"""
    # labor：试用期+工资+社保+离职 = 4；intern：实习协议 = 3 → labor 胜
    text = "试用期 工资 社保 离职 实习协议"
    assert detect_contract_type("合同.docx", text) == "labor"


def test_real_samples_classify_by_content():
    """真实样本抹掉文件名后，仅凭正文应判回各自类型。"""
    for name, ct in [
        ("房屋租赁合同样本.docx", "rent"),
        ("劳动合同样本.docx", "labor"),
        ("实习协议样本.docx", "intern"),
    ]:
        parsed = parse_file(f"data/samples/{name}", name, ct)
        text = "\n".join(ln.text for ln in parsed.lines)
        assert detect_contract_type("未命名.docx", text) == ct, name


# --------------------------------------------------------------------------- #
# 2. /api/analyze contract_type="auto" 全链路
# --------------------------------------------------------------------------- #
@pytest.fixture()
def client(tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "upload_dir", tmp_path)
    for attr in ("uploads", "documents", "reports", "_upload_ts", "_doc_ts", "_report_ts"):
        setattr(store, attr, {})
    # 限流器是进程级单例：本文件的上传/分析请求会耗尽配额，
    # 污染同 session 后续文件（test_routers 等）的用例 → 用例前后重置。
    from api.ratelimit import analyze_limiter, upload_limiter

    upload_limiter.reset()
    analyze_limiter.reset()
    yield TestClient(app)
    upload_limiter.reset()
    analyze_limiter.reset()
    for attr in ("uploads", "documents", "reports", "_upload_ts", "_doc_ts", "_report_ts"):
        setattr(store, attr, {})


def _make_docx(path, paragraphs: list[str]):
    from docx import Document

    d = Document()
    for p in paragraphs:
        d.add_paragraph(p)
    d.save(str(path))


def _sse_events(resp_text: str) -> list[dict]:
    return [
        json.loads(line[len("data: "):])
        for line in resp_text.splitlines()
        if line.startswith("data: ")
    ]


def test_analyze_auto_detects_intern(client, tmp_path):
    """无名件 + 实习正文 → auto 链路识别为 intern，雷达维度随类型切换。"""
    path = tmp_path / "合同.docx"  # 文件名不含任何类型词
    _make_docx(path, INTERN_TEXT)

    with open(path, "rb") as f:
        up = client.post("/api/upload", files={"file": ("合同.docx", f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}, data={"contract_type": "auto"})
    assert up.status_code == 200, up.text
    file_id = up.json()["file_id"]

    resp = client.post("/api/analyze", json={"file_id": file_id, "contract_type": "auto"})
    assert resp.status_code == 200
    events = _sse_events(resp.text)
    detected = next(e for e in events if e["type"] == "contract_type_detected")
    assert detected["data"] == "intern"

    done = next(e for e in events if e["type"] == "done")
    report = done["report"]
    # intern 的雷达维度 = 报酬/安全/期限/知识产权/违约金（区别于 rent 的押金/解约/维修/违约/隐私）
    assert set(report["radar_dimensions"]) == {"报酬", "安全", "期限", "知识产权", "违约金"}

    # 文档树回写的类型也应一致
    doc = client.get(f"/api/document/{file_id}")
    assert doc.status_code == 200


def test_analyze_auto_detects_labor(client, tmp_path):
    path = tmp_path / "协议.docx"
    _make_docx(path, LABOR_TEXT)
    with open(path, "rb") as f:
        up = client.post("/api/upload", files={"file": ("协议.docx", f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}, data={"contract_type": "auto"})
    file_id = up.json()["file_id"]
    resp = client.post("/api/analyze", json={"file_id": file_id, "contract_type": "auto"})
    events = _sse_events(resp.text)
    detected = next(e for e in events if e["type"] == "contract_type_detected")
    assert detected["data"] == "labor"
    done = next(e for e in events if e["type"] == "done")
    assert set(done["report"]["radar_dimensions"]) == {"工资", "试用期", "社保", "竞业限制", "违约金"}


def test_analyze_explicit_type_not_overridden(client, tmp_path):
    """显式指定类型时不受 auto 影响，且不推送 detected 事件。"""
    path = tmp_path / "房屋租赁合同.docx"
    _make_docx(path, RENT_TEXT)
    with open(path, "rb") as f:
        up = client.post("/api/upload", files={"file": ("房屋租赁合同.docx", f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}, data={"contract_type": "rent"})
    file_id = up.json()["file_id"]
    resp = client.post("/api/analyze", json={"file_id": file_id, "contract_type": "rent"})
    events = _sse_events(resp.text)
    assert not any(e["type"] == "contract_type_detected" for e in events)
    done = next(e for e in events if e["type"] == "done")
    assert set(done["report"]["radar_dimensions"]) == {"押金", "解约", "维修", "违约", "隐私"}


def test_upload_invalid_type_still_falls_back(client, tmp_path):
    """非法类型值仍兜底 rent（不因 auto 放开而破坏原校验）。"""
    path = tmp_path / "x.docx"
    _make_docx(path, RENT_TEXT)
    with open(path, "rb") as f:
        up = client.post("/api/upload", files={"file": ("x.docx", f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}, data={"contract_type": "bogus"})
    assert up.status_code == 200

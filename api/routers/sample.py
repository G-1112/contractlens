"""示例合同下载（评委零门槛体验，提交要求「点开链接即可试用」）。

演示场景：
- loan            借款合同（标准样例，含 7 项高风险，可看全流程）
- loan_injection  借款合同（内嵌提示注入攻击，安全演示）
- rent            房屋租赁合同
- labor           劳动合同
- intern          实习协议
- guarantee       保证合同

安全：key 白名单 + 文件只读；文件来自 data/samples/ 的预置样例，
不含任何真实个人信息。
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()

DATA = Path(__file__).resolve().parents[2] / "data" / "samples"

SAMPLES: dict[str, dict] = {
    "loan": {"file": "借款合同样本.docx", "name": "借款合同样本.docx", "type": "loan"},
    "loan_injection": {
        "file": "借款合同-注入攻击样本.docx",
        "name": "借款合同-注入攻击样本.docx",
        "type": "loan",
    },
    "rent": {"file": "房屋租赁合同样本.docx", "name": "房屋租赁合同样本.docx", "type": "rent"},
    "labor": {"file": "劳动合同样本.docx", "name": "劳动合同样本.docx", "type": "labor"},
    "intern": {"file": "实习协议样本.docx", "name": "实习协议样本.docx", "type": "intern"},
    "guarantee": {"file": "保证合同样本.docx", "name": "保证合同样本.docx", "type": "guarantee"},
}


@router.get("/api/sample/{key}")
def get_sample(key: str) -> FileResponse:
    meta = SAMPLES.get(key)
    if meta is None:
        raise HTTPException(status_code=404, detail="未知示例")
    path = DATA / meta["file"]
    if not path.exists():
        raise HTTPException(status_code=404, detail="示例文件缺失")
    return FileResponse(
        path,
        filename=meta["name"],
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

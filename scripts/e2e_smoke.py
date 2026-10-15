"""端到端冒烟测试：上传 → 分析 → 追问，走真实 HTTP 接口。

需要先启动后端：uvicorn api.main:app --port 8000

覆盖三条关键路径：
  1. 借款合同：完整审查链路，校验风险条目与法条引用
  2. 注入攻击样本：校验 R-INJ-01 是否触发文档级 flagged
  3. 追问对话：校验 /api/chat 返回带 sources 与 trace 的回答

用法：python scripts/e2e_smoke.py [base_url]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "data" / "samples"
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"


def make_client() -> httpx.Client:
    """本地回环直连，禁用环境代理。

    注意：httpx 默认 trust_env=True，会读取 HTTP_PROXY/HTTPS_PROXY 环境变量。
    沙箱/CI 环境常注入 127.0.0.1:<port> 的本地代理，代理对同一 keep-alive
    连接的第二个请求起会返回 404，表现为「首次 200、之后全部 404」的假故障。
    本地 127.0.0.1 无需代理，故显式关闭。
    """
    return httpx.Client(trust_env=False)


def upload(client: httpx.Client, path: Path, ctype: str) -> str:
    with path.open("rb") as f:
        r = client.post(
            f"{BASE}/api/upload",
            files={"file": (path.name, f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            data={"contract_type": ctype},
            timeout=60,
        )
    r.raise_for_status()
    return r.json()["file_id"]


def analyze(client: httpx.Client, file_id: str, ctype: str) -> dict:
    """消费 SSE 流，返回最后的 done 事件中的 report。"""
    report: dict = {}
    with client.stream(
        "POST", f"{BASE}/api/analyze", json={"file_id": file_id, "contract_type": ctype}, timeout=120
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            if ev.get("type") == "done":
                report = ev["report"]
            elif ev.get("type") == "error":
                print(f"  [error] {ev}")
    return report


def chat(client: httpx.Client, file_id: str, question: str) -> dict:
    r = client.post(f"{BASE}/api/chat", json={"file_id": file_id, "question": question}, timeout=60)
    r.raise_for_status()
    return r.json()


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    with make_client() as client:
        # 0) 健康检查
        h = client.get(f"{BASE}/api/health", timeout=10).json()
        results.append(("健康检查", h.get("status") == "ok", f"llm_enabled={h.get('llm_enabled')}"))

        # 1) 借款合同完整链路
        fid = upload(client, SAMPLES / "借款合同样本.docx", "loan")
        rep = analyze(client, fid, "loan")
        counts = rep.get("risk_counts", {})
        ok1 = rep.get("clause_count", 0) > 0 and counts.get("高", 0) > 0
        results.append(
            (
                "借款合同审查",
                ok1,
                f"条款 {rep.get('clause_count')} / 高 {counts.get('高')} 中 {counts.get('中')} / 总分 {rep.get('total_score')}",
            )
        )
        has_reg = any(it.get("regulation_refs") for it in rep.get("items", []) if it.get("final_level") != "无")
        results.append(("法条引用", has_reg, "至少一条风险附带法规引用" if has_reg else "无风险条目带法规引用"))

        # 2) 注入攻击样本
        fid2 = upload(client, SAMPLES / "借款合同-注入攻击样本.docx", "loan")
        rep2 = analyze(client, fid2, "loan")
        flagged = rep2.get("injection_flagged", False)
        results.append(("注入攻击检出（R-INJ-01）", flagged, f"injection_flagged={flagged}"))

        # 3) 追问对话
        resp = chat(client, fid, "第4条约定的年利率是否合法？请引用具体法条说明")
        ok3 = bool(resp.get("answer")) and len(resp.get("sources", [])) > 0
        results.append(
            (
                "追问对话 /api/chat",
                ok3,
                f"mode={resp.get('mode')} / sources {len(resp.get('sources', []))} 条 / trace {len(resp.get('trace', []))} 步",
            )
        )

    print("\n=== 端到端冒烟结果 ===")
    print(f"| 用例 | 结果 | 详情 |")
    print(f"|---|---|---|")
    for name, ok, detail in results:
        print(f"| {name} | {'PASS' if ok else 'FAIL'} | {detail} |")

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n合计 {len(results)} 项，通过 {len(results) - len(failed)}，失败 {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

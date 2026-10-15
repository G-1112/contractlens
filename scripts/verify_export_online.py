"""线上导出链路复验（对应提交 0a422dd 的修复）。

背景：此前 5 个导出按钮全部 404，因为路由只注册 POST 而前端用 <a href>（GET）。
本脚本对线上部署走一遍真实浏览器路径：上传 → 分析 → 对每种 format 发 GET。

断言：
- md / revised / checklist：200 且返回文本含合同关键内容
- pdf：200、Content-Type application/pdf、正文以 %PDF- 魔数开头
- docx：200、正文以 PK zip 魔数开头
- GET 与 POST 对 pdf 结果一致（同一 handler 双注册）

用法：python scripts/verify_export_online.py [BASE_URL]
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "data" / "samples"
BASE = sys.argv[1] if len(sys.argv) > 1 else "https://contractlens-agent.app.workbuddy.host"


def make_client() -> httpx.Client:
    # 沙箱注入 HTTP_PROXY，httpx 默认 trust_env=True 会走代理导致假故障，必须关
    return httpx.Client(trust_env=False, timeout=60)


def upload(client: httpx.Client, path: Path, ctype: str) -> str:
    with path.open("rb") as f:
        r = client.post(
            f"{BASE}/api/upload",
            files={"file": (path.name, f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            data={"contract_type": ctype},
        )
    r.raise_for_status()
    return r.json()["file_id"]


def analyze(client: httpx.Client, file_id: str, ctype: str) -> dict:
    r = client.post(f"{BASE}/api/analyze", json={"file_id": file_id, "contract_type": ctype}, timeout=180)
    r.raise_for_status()
    # SSE 流式响应，末行 event=done 带完整结果；直接轮询 result 更稳
    import time
    for _ in range(120):
        g = client.get(f"{BASE}/api/result/{file_id}", timeout=30)
        if g.status_code == 200:
            return g.json()
        time.sleep(2)
    raise RuntimeError(f"分析未完成：{r.status_code}")


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    with make_client() as client:
        h = client.get(f"{BASE}/api/health", timeout=15).json()
        results.append(("健康检查", h.get("status") == "ok", f"llm_enabled={h.get('llm_enabled')}"))

        fid = upload(client, SAMPLES / "借款合同样本.docx", "loan")
        results.append(("上传样本", True, f"file_id={fid[:8]}…"))

        rep = analyze(client, fid, "loan")
        counts = rep.get("risk_counts", {})
        results.append(
            (
                "分析完成",
                rep.get("clause_count", 0) > 0,
                f"条款 {rep.get('clause_count')} / 高 {counts.get('高')} 中 {counts.get('中')} / 总分 {rep.get('total_score')}",
            )
        )

        # 1) 三种文本格式 GET 导出（前端 <a href> 的真实路径）
        for fmt, needle in [
            ("md", "合同审查报告"),
            ("revised", "修订"),
            ("checklist", "整改"),
        ]:
            r = client.get(f"{BASE}/api/report/{fid}", params={"format": fmt}, timeout=60)
            body = r.text
            ok = r.status_code == 200 and needle in body and len(body) > 500
            results.append((f"GET 导出 {fmt}", ok, f"{r.status_code} / {len(body)} 字符"))

        # 2) PDF：核心断言——魔数 + Content-Type
        r = client.get(f"{BASE}/api/report/{fid}", params={"format": "pdf"}, timeout=120)
        pdf_ok = (
            r.status_code == 200
            and r.content[:5] == b"%PDF-"
            and "pdf" in r.headers.get("content-type", "").lower()
        )
        results.append(
            (
                "GET 导出 pdf",
                pdf_ok,
                f"{r.status_code} / {r.headers.get('content-type')} / {len(r.content)} bytes / 魔数 {'OK' if r.content[:5] == b'%PDF-' else r.content[:8]!r}",
            )
        )

        # 3) DOCX
        r = client.get(f"{BASE}/api/report/{fid}", params={"format": "docx"}, timeout=120)
        docx_ok = r.status_code == 200 and r.content[:2] == b"PK"
        results.append(("GET 导出 docx", docx_ok, f"{r.status_code} / {len(r.content)} bytes"))

        # 4) GET/POST 双注册一致性（pdf）
        r_post = client.post(f"{BASE}/api/report/{fid}", params={"format": "pdf"}, timeout=120)
        same = r_post.status_code == 200 and r_post.content[:5] == b"%PDF-"
        results.append(("POST 导出 pdf（双注册）", same, f"{r_post.status_code}"))

        # 5) 404 路径：不存在的 file_id 应返回 404 而非 500
        r = client.get(f"{BASE}/api/report/deadbeef", params={"format": "pdf"}, timeout=30)
        results.append(("未知 file_id → 404", r.status_code == 404, f"HTTP {r.status_code}"))

    print(f"\n=== 线上导出链路复验（{BASE}） ===")
    print("| 用例 | 结果 | 详情 |")
    print("|---|---|---|")
    for name, ok, detail in results:
        print(f"| {name} | {'PASS' if ok else 'FAIL'} | {detail} |")

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n合计 {len(results)} 项，通过 {len(results) - len(failed)}，失败 {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

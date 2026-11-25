"""提交前可用性验证（PPT 第 18 页承诺「提交前 48h / 24h / 1h 三次验证」）。

对任意 base_url 跑一遍评委实际会走的完整链路，输出可归档的 markdown 记录：
  1. 健康检查（含 LLM 是否启用）
  2. 前端页面可达 + 资源加载
  3. 评审访问门禁状态（是否要求口令）
  4. 上传 → 分析 → 结果
  5. 五种格式导出（PDF 校验 %PDF- 魔数）
  6. 追问对话（sources 可追溯引用）
  7. 提示注入样本检出

用法：
  python scripts/availability_check.py --label T-48h
  python scripts/availability_check.py --label 冒烟 --base http://127.0.0.1:8000
  # 公网已启用评审口令时（--code 缺省读环境变量 REVIEW_ACCESS_CODE）：
  REVIEW_ACCESS_CODE=CL-xxxx-xxxx python scripts/availability_check.py --label T-48h
  python scripts/availability_check.py --label T-48h --code CL-xxxx-xxxx

退出码 0 = 全部通过；1 = 有失败项（提交前必须为 0）。
"""

from __future__ import annotations

import os

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "data" / "samples"
DEFAULT_BASE = "https://contractlens-agent.app.workbuddy.host"


def client(code: str = "") -> httpx.Client:
    # 沙箱注入 HTTP_PROXY，httpx 默认 trust_env=True 会走代理导致同连接第2 个
    # 请求起全404 的假故障（曾据此误判上传接口有问题），必须关。
    # 公网启用 REVIEW_ACCESS_CODE 后，业务端点需带口令头，故支持 code 注入。
    headers = {"X-Review-Code": code} if code else {}
    return httpx.Client(trust_env=False, timeout=120, headers=headers)


def q(base: str, path: str) -> str:
    """拼接 URL：平台代理要求每个请求带 sharecode（2026-11-24 重新部署实测，
    裸域名返回 403「链接不完整」页）；sharecode 从环境变量或 --sharecode 传入。"""
    import os as _os

    sc = _os.environ.get("SITES_SHARECODE", "").strip()
    if not sc:
        return base + path
    sep = "&" if "?" in path else "?"
    return base + path + f"{sep}sharecode={sc}"


def upload(cl: httpx.Client, base: str, path: Path, ctype: str) -> str:
    with path.open("rb") as f:
        r = cl.post(
            q(base, "/api/upload"),
            files={"file": (path.name, f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            data={"contract_type": ctype},
        )
    r.raise_for_status()
    return r.json()["file_id"]


def analyze(cl: httpx.Client, base: str, fid: str, ctype: str, budget_s: int = 180) -> dict:
    """跑分析并返回报告 + 真实端到端耗时。

    计时口径注意：/api/analyze 是 SSE 流式接口，POST 本身会**阻塞到流结束**，
    所以端到端耗时必须记在 POST 上；POST 返回后再轮询 result 通常是 0s。
    （初版把耗时记在轮询上，导致报告里出现「等待 0.0s」的假数据。）
    """
    t0 = time.perf_counter()
    cl.post(q(base, "/api/analyze"), json={"file_id": fid, "contract_type": ctype}, timeout=budget_s)
    post_ms = (time.perf_counter() - t0) * 1000
    for _ in range(budget_s):
        g = cl.get(q(base, f"/api/result/{fid}"))
        if g.status_code == 200:
            # 注意：必须复用同一个 dict。g.json() 每次调用都会重新解析出一个新
            # 对象，写入后 return g.json() 会把刚加的键丢掉（初版因此误报 0.0s）。
            data = g.json()
            data["_wait_ms"] = post_ms
            return data
        time.sleep(1)
    raise TimeoutError(f"分析在 {budget_s}s 内未完成")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--label", default="未标注", help="本轮标签，如 T-48h / T-24h / T-1h")
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "可用性验证记录.md")
    ap.add_argument(
        "--code",
        default=os.environ.get("REVIEW_ACCESS_CODE", ""),
        help="评审访问口令（启用门禁时必填；缺省读环境变量 REVIEW_ACCESS_CODE）",
    )
    args = ap.parse_args()

    base = args.base.rstrip("/")
    rows: list[tuple[str, bool, str]] = []
    ts = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    with client(args.code) as cl:
        # 1) 健康检查
        try:
            h = cl.get(q(base, "/api/health"), timeout=15).json()
            rows.append(("健康检查", h.get("status") == "ok",
                         f"llm_enabled={h.get('llm_enabled')} model={h.get('llm_model')}"))
        except Exception as e:  # noqa: BLE001
            rows.append(("健康检查", False, f"{type(e).__name__}: {e}"))
            print("[!] 健康检查失败，后续用例跳过")
            _emit(rows, ts, args, base)
            return 1

        # 2) 前端页面 + 资源
        try:
            idx = cl.get(q(base, "/"), timeout=20)
            html = idx.text
            has_root = "<div id=\"root\"" in html or 'id="root"' in html
            asset_ok = "/assets/" in html
            rows.append(("前端页面可达", idx.status_code == 200 and has_root and asset_ok,
                         f"HTTP {idx.status_code}, {len(html)} 字符, 挂载产物={asset_ok}"))
        except Exception as e:  # noqa: BLE001
            rows.append(("前端页面可达", False, f"{type(e).__name__}: {e}"))

        # 3) 门禁状态（探测性：**显式清空口令**打一个业务端点看是否 401）
        #    注意：不能复用带口令的 client——那样探针会拿到 404，把已启用的
        #    门禁误报成「未启用」（2026-10-31 实测踩到）。
        try:
            probe = cl.get(
                q(base, "/api/result/probe"),
                headers={"X-Review-Code": ""},
                timeout=15,
            )
            gated = probe.status_code == 401
            rows.append(("评审访问门禁", True,
                         "已启用（无口令返回 401）" if gated else "未启用（口令未配置）"))
        except Exception as e:  # noqa: BLE001
            rows.append(("评审访问门禁", False, f"{type(e).__name__}: {e}"))

        # 4) 上传 → 分析
        try:
            fid = upload(cl, base, SAMPLES / "借款合同样本.docx", "loan")
            rep = analyze(cl, base, fid, "loan")
            counts = rep.get("risk_counts", {})
            rows.append(("上传→分析", rep.get("clause_count", 0) > 0,
                         f"条款 {rep.get('clause_count')} / 高 {counts.get('高')} 中 {counts.get('中')} 低 {counts.get('低')}"
                         f" / 总分 {rep.get('total_score')} / 等待 {rep.get('_wait_ms', 0)/1000:.1f}s"))
        except Exception as e:  # noqa: BLE001
            rows.append(("上传→分析", False, f"{type(e).__name__}: {e}"))
            _emit(rows, ts, args, base)
            return 1

        # 5) 五格式导出
        for fmt, probe_ok in [
            ("md", lambda b: len(b) > 500),
            ("revised", lambda b: len(b) > 500),
            ("checklist", lambda b: len(b) > 300),
            ("pdf", lambda b: b[:5] == b"%PDF-"),
            ("docx", lambda b: b[:2] == b"PK"),
        ]:
            try:
                t0 = time.perf_counter()
                r = cl.get(q(base, f"/api/report/{fid}"), params={"format": fmt})
                body = r.content
                dt = (time.perf_counter() - t0) * 1000
                ok = r.status_code == 200 and probe_ok(body)
                rows.append((f"导出 {fmt}", ok,
                             f"HTTP {r.status_code} / {len(body)} bytes / {dt:.0f}ms / {r.headers.get('content-type','')}"))
            except Exception as e:  # noqa: BLE001
                rows.append((f"导出 {fmt}", False, f"{type(e).__name__}: {e}"))

        # 6) 追问对话
        try:
            r = cl.post(q(base, "/api/chat"),
                        json={"file_id": fid, "question": "第4条约定的年利率是否合法？请引用具体法条说明"},
                        timeout=90)
            d = r.json()
            rows.append(("追问对话", r.status_code == 200 and len(d.get("sources", [])) > 0,
                         f"mode={d.get('mode')} / sources {len(d.get('sources', []))} / trace {len(d.get('trace', []))}"))
        except Exception as e:  # noqa: BLE001
            rows.append(("追问对话", False, f"{type(e).__name__}: {e}"))

        # 7) 注入检出
        try:
            fid2 = upload(cl, base, SAMPLES / "借款合同-注入攻击样本.docx", "loan")
            rep2 = analyze(cl, base, fid2, "loan")
            rows.append(("提示注入检出（R-INJ-01）", bool(rep2.get("injection_flagged")),
                         f"injection_flagged={rep2.get('injection_flagged')}"))
        except Exception as e:  # noqa: BLE001
            rows.append(("提示注入检出（R-INJ-01）", False, f"{type(e).__name__}: {e}"))

        # 8) 登录接口（R-AUTH-02，第十四轮）：正确凭证 200 / 错误密码 401 / 错误账号 401
        def _post_login(u: str, p: str):
            r = cl.post(q(base, "/api/login"), json={"username": u, "password": p}, timeout=30)
            return r.status_code, (r.json() if r.status_code == 200 else r.json().get("detail", ""))

        try:
            st, body = _post_login("reviewer", args.code)
            tok = body.get("token", "") if isinstance(body, dict) else ""
            # 第十六轮：token 应为 HMAC 会话令牌（含签名分隔点、非明文口令）
            ok = st == 200 and "." in tok and tok != args.code
            rows.append(("登录·正确凭证（会话令牌）", ok,
                         f"HTTP {st} / {'会话令牌' if ok else 'token 异常'}"))
        except Exception as e:  # noqa: BLE001
            rows.append(("登录·正确凭证（会话令牌）", False, f"{type(e).__name__}: {e}"))
        try:
            _, body = _post_login("reviewer", args.code)
            tok = body.get("token", "") if isinstance(body, dict) else ""
            pr = cl.get(q(base, "/api/result/__probe__"), headers={"X-Review-Session": tok}, timeout=30)
            rows.append(("会话令牌过门禁", pr.status_code == 404,
                         f"HTTP {pr.status_code}（404=通过门禁）"))
        except Exception as e:  # noqa: BLE001
            rows.append(("会话令牌过门禁", False, f"{type(e).__name__}: {e}"))
        try:
            st, detail = _post_login("reviewer", "wrong-password")
            rows.append(("登录·错误密码拒绝", st == 401, f"HTTP {st} / {detail}"))
        except Exception as e:  # noqa: BLE001
            rows.append(("登录·错误密码拒绝", False, f"{type(e).__name__}: {e}"))
        try:
            st, detail = _post_login("hacker", args.code)
            rows.append(("登录·错误账号拒绝", st == 401, f"HTTP {st} / {detail}"))
        except Exception as e:  # noqa: BLE001
            rows.append(("登录·错误账号拒绝", False, f"{type(e).__name__}: {e}"))

    return _emit(rows, ts, args, base)


def _emit(rows, ts, args, base) -> int:
    failed = [n for n, ok, _ in rows if not ok]
    block = [
        f"\n## {args.label}　{ts}\n",
        f"目标：`{base}`\n",
        "| 用例 | 结果 | 详情 |",
        "|---|---|---|",
    ]
    for name, ok, detail in rows:
        block.append(f"| {name} | {'PASS' if ok else 'FAIL'} | {detail} |")
    block.append("")
    block.append(f"小计 {len(rows)} 项，通过 {len(rows)-len(failed)}，失败 {len(failed)}")
    block.append("")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# 可用性验证记录\n\n"
        "> 用途：PPT 第 18 页承诺「提交前 48h / 24h / 1h 三次可用性验证」。\n"
        "> 生成脚本：`scripts/availability_check.py`；每轮追加一节。\n"
        "> 通过标准：全部 PASS。任一 FAIL 应先修复再提交（演示视频为兜底）。\n"
    )
    if args.out.exists():
        prev = args.out.read_text(encoding="utf-8")
        if header.split("\n")[0] not in prev:
            prev = header + prev
        args.out.write_text(prev + "\n".join(block), encoding="utf-8")
    else:
        args.out.write_text(header + "\n".join(block), encoding="utf-8")

    print("\n".join(block))
    print(f"记录已写入：{args.out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
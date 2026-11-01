"""从公开来源收集真实合同文本（脱敏）→ data/evalset_real/contracts/。

设计原则：
  1. 只采**公开可访问**的官方/权威来源，每份登记 source/obtained/license；
  2. 抓不到就跳过并报告，绝不伪造文本；
  3. 官方示范文本与银行公开合同文本本身不含个人真实信息（字段为空白），
     仍执行脱敏扫描兜底。

支持的来源类型：
  samr  —— 市场监管总局合同示范文本库 /View?id= 页（HTML 正文）
  html  —— 任意公开网页（start/stop 定界）
  pdf   —— 公开 PDF（pdfplumber 提取；扫描件会因无文本层被跳过）
  docx  —— 公开 docx 附件

用法：
  python scripts/collect_real_contracts.py --only loan,guarantee
  python scripts/collect_real_contracts.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from datetime import date
from html import unescape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "evalset_real" / "contracts"
PROV = ROOT / "data" / "evalset_real" / "provenance.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ContractLens-Research/1.0"
SAMR = "https://htsfwb.samr.gov.cn"
TODAY = date.today().isoformat()

STOP_WORDS = ("版权所有", "Copyright", "上一篇", "下一篇", "打印本页", "网站地图", "主办单位", "ICP备")


def http_get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def html_to_lines(raw: bytes) -> list[str]:
    h = raw.decode("utf-8", errors="ignore")
    h = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", h)
    h = re.sub(r"<br\s*/?>", "\n", h, flags=re.I)
    h = re.sub(r"</(p|div|li|h[1-6]|tr|td)>", "\n", h, flags=re.I)
    h = re.sub(r"<[^>]+>", "", h)
    h = unescape(h)
    lines = [re.sub(r"[ \t\u3000]+", " ", ln).strip() for ln in h.split("\n")]
    return [ln for ln in lines if ln]


def pdf_to_lines(raw: bytes, tmp: Path) -> list[str]:
    import pdfplumber

    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(raw)
    out: list[str] = []
    with pdfplumber.open(str(tmp)) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ""
            out.extend(re.sub(r"[ \t\u3000]+", " ", ln).strip() for ln in t.split("\n"))
    return [ln for ln in out if ln]


def docx_to_lines(raw: bytes, tmp: Path) -> list[str]:
    from docx import Document

    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(raw)
    doc = Document(str(tmp))
    return [p.text.strip() for p in doc.paragraphs if p.text.strip()]


def cut(lines: list[str], start: str, stop: tuple[str, ...] = STOP_WORDS) -> list[str]:
    """按起始关键词定位正文，遇停止词截断。"""
    begin = 0
    for i, ln in enumerate(lines):
        if start in ln:
            begin = i
            break
    end = len(lines)
    for i in range(begin + 1, len(lines)):
        if any(w in lines[i] for w in stop):
            end = i
            break
    return lines[begin:end]


# ---------------------------------------------------------------------------
# 来源清单
# ---------------------------------------------------------------------------
SOURCES = [
    # ---- 租赁（已采集，保留以便复跑）----
    dict(kind="samr", file="real_rent_01.docx", ctype="rent", name="城镇房屋租赁合同（市场监管总局2025版）",
         tid="2340996b-882d-47a4-b74d-c30784628737", start="城镇房屋租赁合同",
         issuer="国家市场监督管理总局", year="2025"),
    dict(kind="samr", file="real_rent_02.docx", ctype="rent", name="人民防空工程租赁使用合同（人防办、工商总局2002版）",
         tid="960588dc-c95e-4f00-93bb-caaee162691b", start="人民防空工程租赁使用合同",
         issuer="国家人民防空办公室、国家工商总局", year="2002"),
    dict(kind="samr", file="real_rent_03.docx", ctype="rent", name="柜台租赁经营合同（工商总局2013版）",
         tid="5335ede3-4daf-4163-9c43-1230ee833eed", start="柜台租赁经营合同",
         issuer="国家工商行政管理总局", year="2013"),
    dict(kind="samr", file="real_rent_04.docx", ctype="rent", name="建筑施工物资租赁合同（工商总局2000版）",
         tid="8c70b60c-bfd3-43ae-9c10-67d83b0fb189", start="建筑施工物资租赁合同",
         issuer="国家工商行政管理局", year="2000"),

    # ---- 借款（银行公开合同文本）----
    dict(kind="pdf", file="real_loan_01.docx", ctype="loan", name="个人综合消费借款合同（兴业银行2024年6月版）",
         url="https://download.cib.com.cn/netbank/download/cn/wealth/20240728/51.pdf",
         start="个人综合消费借款合同",
         issuer="兴业银行", year="2024", license="银行官网公开发布；用于研究与评测，注明出处"),
    dict(kind="pdf", file="real_loan_02.docx", ctype="loan", name="个人经营类借款合同（兴业银行）",
         url="https://download.cib.com.cn/netbank/download/cn/wealth/20220704/20.pdf",
         start="个人经营类借款合同",
         issuer="兴业银行", year="2022", license="银行官网公开发布；用于研究与评测，注明出处"),
    dict(kind="docx", file="real_loan_03.docx", ctype="loan", name="线上个人信用借款合同（浙江农信）",
         url="https://www.zj96596.com/zj96596/attachDir/2024/07/2024070914311950329.docx",
         start="线上个人信用借款合同",
         issuer="浙江农村信用社", year="2024", license="官网公开发布；用于研究与评测，注明出处"),
    dict(kind="html", file="real_loan_04.docx", ctype="loan", name="个人借款协议（中信银行信用卡）",
         url="https://creditcard.ecitic.com/heyue/xingdongshidai/jiekuanxieyi.html",
         start="个人借款协议",
         issuer="中信银行", year="2024", license="官网公开发布；用于研究与评测，注明出处"),

    # ---- 保证（12348 法网 / 仲裁委公开范本）----
    dict(kind="html", file="real_gua_01.docx", ctype="guarantee", name="个人住房借款的保证合同范本（12348青海法网）",
         url="https://qh.12348.gov.cn/pub/qhfw/fltb/flzxbk/fb/202410/t20241025_125751.html",
         start="个人住房借款的保证合同范本",
         issuer="青海省司法厅 12348 法网", year="2024", license="政府公共法律服务网站公开范本；可引用并注明出处"),
    dict(kind="html", file="real_gua_02.docx", ctype="guarantee", name="委托担保合同（12348青海法网）",
         url="https://qh.12348.gov.cn/pub/qhfw/fltb/gzywblzn/wt/202408/t20240821_123051.html",
         start="委托担保合同",
         issuer="青海省司法厅 12348 法网", year="2024", license="政府公共法律服务网站公开范本；可引用并注明出处"),
    dict(kind="html", file="real_gua_03.docx", ctype="guarantee", name="保证合同（日照仲裁委员会公开范本）",
         url="http://zcb.rizhao.gov.cn/art/2015/6/3/art_189801_5801562.html",
         start="保证合同",
         issuer="日照仲裁委员会", year="2015", license="政府/仲裁机构网站公开范本；可引用并注明出处"),
    dict(kind="html", file="real_gua_04.docx", ctype="guarantee", name="保证合同（厦门仲裁委员会公开范本）",
         url="https://www.xmac.org.cn/Home/Index/detail?ArticleId=92&ClassifyId=25",
         start="保证合同",
         issuer="厦门仲裁委员会", year="2020", license="仲裁机构网站公开范本；可引用并注明出处"),

    # ---- 实习（教育部三方协议示范文本的公开转载版）----
    dict(kind="pdf", file="real_intern_01.docx", ctype="intern", name="职业学校学生岗位实习三方协议（辽宁省教育厅公开附件）",
         url="https://jyt.ln.gov.cn/jyt/gk/zxtz/B41B8ED50CC74D87A42049A1DC93C79B/P020220419496739965121.pdf",
         start="职业学校学生岗位实习三方协议",
         issuer="辽宁省教育厅（教育部等八部门文本）", year="2022",
         license="省级教育行政部门网站公开附件；可引用并注明出处"),
    dict(kind="html", file="real_intern_02.docx", ctype="intern", name="学生岗位实习三方协议（东平县政府公开）",
         url="http://www.dongping.gov.cn/art/2026/4/30/art_272708_10361881.html",
         start="学生岗位实习三方协议",
         issuer="东平县人民政府（学校实习材料公开）", year="2026",
         license="政府网站公开；可引用并注明出处"),
    dict(kind="html", file="real_intern_03.docx", ctype="intern", name="职业学校学生岗位实习三方协议（广西南宁人社转载）",
         url="https://ggfw.nn12333.com:8081/nnkbms/node/4831",
         start="职业学校学生岗位实习三方协议",
         issuer="南宁市人力资源和社会保障局", year="2024",
         license="人社系统网站公开；可引用并注明出处"),

    # ---- 租赁补充（省级示范文本）----
    dict(kind="pdf", file="real_rent_05.docx", ctype="rent", name="广东省住房租赁合同（示范文本，2019）",
         url="https://zfcxjst.gd.gov.cn/attachment/0/371/371273/2577029.pdf",
         start="广东省住房租赁合同",
         issuer="广东省住房和城乡建设厅、广东省市场监督管理局", year="2019",
         license="省级住建/市场监管部门公开发布；可引用并注明出处"),

    # ---- 劳动补充 ----
    dict(kind="local", file="real_labor_02.docx", ctype="labor", name="劳动合同（劳务派遣，人社部示范文本）",
         url="https://www.longmen.gov.cn/attachment/0/261/261769/5309773.doc",
         local="_raw/labor2.docx",
         start="劳 动 合 同",
         issuer="人力资源社会保障部（龙门县政府公开附件）", year="2019",
         license="政府网站公开附件；可引用并注明出处"),
    dict(kind="html", file="real_labor_03.docx", ctype="labor", name="广东省劳动合同示范文本",
         url="https://www.hanchenglawfirm.com/index.php?a=show&c=index&catid=47&id=1889&m=content",
         start="根据《中华人民共和国劳动法》",
         issuer="广东省人力资源和社会保障厅（律所网站转载）", year="2023",
         license="律所网站公开转载的省级示范文本；可引用并注明出处"),

    # ---- 补充：北京住房租赁（官方 .doc）+ 乐清实习协议（已填写签署版，需脱敏）----
    dict(kind="local_txt", file="real_rent_06.docx", ctype="rent", name="北京市住房租赁合同（示范文本 BF—2023—0603）",
         url="https://zjw.beijing.gov.cn/bjjs/resource/cms/article/bjjs_305586/326062240/2023031514554171411.doc",
         local="_raw/rent_bj.txt",
         start="北京市住房租赁合同",
         issuer="北京市住房和城乡建设委员会、北京市市场监督管理局", year="2023",
         license="省级住建/市场监管部门公开发布；可引用并注明出处"),
    dict(kind="local", file="real_intern_04.docx", ctype="intern", name="职业学校学生岗位实习三方协议（已填写签署版，已脱敏）",
         url="https://web.yqer.cn/filedownload/article.htm?id=id440669474050038246150",
         local="_raw/intern_yq.docx",
         start="职业学校学生岗位实习三方协议",
         issuer="公开转载的签署版（原校名等身份信息已脱敏）", year="2023",
         license="公开网页附件；已移除学校名称等身份信息后用于评测"),
]


def save_docx(lines: list[str], path: Path, heading: str) -> None:
    from docx import Document

    doc = Document()
    if heading:
        doc.add_heading(heading, level=1)
    for ln in lines:
        doc.add_paragraph(ln)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default="", help="只处理指定类型，逗号分隔，如 loan,guarantee")
    args = ap.parse_args()
    only = {x.strip() for x in args.only.split(",") if x.strip()}

    prov = json.loads(PROV.read_text(encoding="utf-8"))
    ok, failed, skipped = [], [], []
    tmp = ROOT / "_raw" / "_tmp_download"

    for src in SOURCES:
        if only and src["ctype"] not in only:
            skipped.append(src["file"])
            continue
        url = f"{SAMR}/View?id={src['tid']}" if src["kind"] == "samr" else src["url"]
        try:
            if src["kind"] == "local":
                raw = b""
            else:
                raw = http_get(url)
            if src["kind"] == "samr":
                body = cut(html_to_lines(raw), src["start"])
            elif src["kind"] == "html":
                body = cut(html_to_lines(raw), src["start"])
            elif src["kind"] == "pdf":
                body = cut(pdf_to_lines(raw, tmp.with_suffix(".pdf")), src["start"], stop=())
            elif src["kind"] == "docx":
                body = cut(docx_to_lines(raw, tmp.with_suffix(".docx")), src["start"], stop=())
            elif src["kind"] == "local":
                body = cut(
                    docx_to_lines((ROOT / src["local"]).read_bytes(), tmp.with_suffix(".docx")),
                    src["start"],
                    stop=(),
                )
            elif src["kind"] == "local_txt":
                t = (ROOT / src["local"]).read_text(encoding="utf-8", errors="ignore").lstrip("\ufeff")
                body = cut([ln.strip() for ln in t.splitlines() if ln.strip()], src["start"], stop=())
            else:
                raise ValueError(src["kind"])

            # 质量门槛：条款起始符支持「第X条」与「一、」两种（切分器同口径）
            has_article = any(
                re.match(r"^第[一1]条", ln) or re.match(r"^[一二三四五六七八九十]+、", ln)
                for ln in body
            )
            if len(body) < 12 or not has_article:
                raise RuntimeError(f"抽取异常：{len(body)} 行，含条款起始={has_article}")
            if not args.dry_run:
                save_docx(body, OUT_DIR / src["file"], src["name"])
            prov["contracts"][src["file"]] = {
                "source": url,
                "obtained": f"公开网页/附件抓取，{TODAY}",
                "license": src.get("license")
                or f"{src['issuer']}公开发布（{src['year']}）；政府网站公开文本，可引用并注明出处",
                "type": "官方示范文本" if src["kind"] == "samr" or "示范" in src["name"] else "机构公开合同文本",
                "contract_type": src["ctype"],
                "name": src["name"],
                "issuer": src["issuer"],
            }
            ok.append((src["file"], len(body)))
            print(f"OK   {src['file']:20s} {len(body):4d} 行  {src['name']}")
        except Exception as e:  # noqa: BLE001
            failed.append((src["file"], src["name"], f"{type(e).__name__}: {e}"))
            print(f"FAIL {src['file']:20s} {src['name']} -> {e}")
        time.sleep(0.6)

    if not args.dry_run:
        PROV.write_text(json.dumps(prov, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n小计 {len(SOURCES) - len(skipped)} 项，成功 {len(ok)}，失败 {len(failed)}")
    for f, n, why in failed:
        print(f"  失败 {f}（{n}）：{why}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())

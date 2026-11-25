"""权威法规全文抓取与条号索引（第十八轮，R-DOC-01 法条核验扩容）。

目标：把 docs/法条核验报告.md 里 113 条「待核」压到最少。
方法：抓取权威渠道全文（gov.cn / 新华社受权发布 / 住建部官网 / 省教育厅等
政府站点），按「第X条」切分出 条号→原文 索引；意见类无条号，保留全文供
containment 比对。核验仍由 scripts/verify_regulations.py 执行（--apply）。

来源与用途（全部为政府/官方渠道）：
  1. 民法典（全文，新华社受权播发）      → 民法典·合同编/总则/人格权编
  2. 劳动合同法 2012 修正（无锡市数据局政府站转载）→ 劳动合同法
  3. 职业学校学生实习管理规定（浙江省教育厅）→ 职业学校学生实习管理规定
  4. 关于整顿规范住房租赁市场秩序的意见（住建部官网）→ containment
  5. 关于加强轻资产住房租赁企业监管的意见（gov.cn）→ containment
  6. 消费者权益保护法 / 劳动法 / 社会保险法 / 个人信息保护法（gov.cn）→ 按条号

输出：data/regulations/_authority/fulltext_*.txt（原文留档）
      data/regulations/_authority/index.json（法规名 → {条号: 原文} 或 {full: 全文}）

用法：python scripts/fetch_authority_laws.py
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "regulations" / "_authority"
OUT.mkdir(parents=True, exist_ok=True)

SOURCES = [
    ("民法典", "http://gongbao.court.gov.cn/Details/74143ea5b6d1d47f82d6759fc6ef17.html"),
    ("劳动合同法", "https://bigdata.wuxi.gov.cn/doc/2023/01/04/4267449.shtml"),
    ("职业学校学生实习管理规定", "https://jyt.zj.gov.cn/art/2023/8/21/art_1229679379_58940646.html"),
    ("关于整顿规范住房租赁市场秩序的意见", "https://www.mohurd.gov.cn/gongkai/zc/wjk/art/2019/art_17339_243241.html"),
    ("关于加强轻资产住房租赁企业监管的意见", "https://www.gov.cn/zhengce/zhengceku/2021-04/27/content_5602856.htm"),
    ("消费者权益保护法", "https://www.gov.cn/flfg/2013-10/25/content_2515601.htm"),
    ("中华人民共和国劳动法", "https://www.gov.cn/banshi/2005-05/25/content_905.htm"),
    ("社会保险法", "https://www.gov.cn/flfg/2010-10/28/content_1732964.htm"),
    ("个人信息保护法", "https://www.gov.cn/xinwen/2021-08/20/content_5632486.htm"),
    ("职工带薪年休假条例", "https://www.gov.cn/zwgk/2007-12/16/content_835285.htm"),
]


def strip_html(b: bytes) -> str:
    for enc in ("utf-8", "gbk", "gb18030"):
        try:
            t = b.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        t = b.decode("utf-8", "replace")
    t = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", t, flags=re.I)
    t = re.sub(r"<[^>]+>", "\n", t)
    t = t.replace("&ensp;", " ").replace("&emsp;", " ").replace("&nbsp;", " ")
    t = re.sub(r"[ \t\r\f\v]+", "", t)
    t = re.sub(r"\n{2,}", "\n", t)
    return t


CN_NUM = "[一二三四五六七八九十百零〇]+"
ART_RE = re.compile(rf"第({CN_NUM})条")


_CN_DIG = {"零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9}


def cn_to_int(cn: str) -> int | None:
    """中文数字 → int（支持 十/百 组合，如 十七/一百二十六/二百零三）。"""
    if not cn or not re.fullmatch(r"[一二三四五六七八九十百零〇]+", cn):
        return None
    cn = cn.replace("〇", "零")
    if cn == "十":
        return 10
    if "百" in cn:
        bai_parts = cn.split("百")
        if len(bai_parts) != 2:
            return None
        head, tail = bai_parts
        h = _CN_DIG.get(head, None) if head else 1
        if h is None or (head and len(head) > 1 and head != "十" and "十" not in head):
            # 处理「一百二十」式的 head
            h = _cn_to_int(head) if head else 1
            if h is None:
                return None
        rest = _cn_to_int(tail) if tail else 0
        if tail and rest is None:
            return None
        return h * 100 + (rest or 0)
    if "十" in cn:
        a, b = cn.split("十", 1)
        tens = _CN_DIG.get(a, 1) if a else 1
        ones = _CN_DIG.get(b, 0) if b else 0
        if (a and a not in _CN_DIG) or (b and b not in _CN_DIG):
            return None
        return tens * 10 + ones
    if len(cn) == 1 and cn in _CN_DIG:
        return _CN_DIG[cn]
    return None


def parse_articles(text: str) -> dict[str, str]:
    """切分 条号→原文，按条号顺序递增跟踪，过滤正文内的条号引用。

    第十八轮修复：正文里会引用其它条号（「依照本法第十七条的规定」），
    朴素切分把正文拦腰截断、条号错位。这里按「序号必须严格 +1」跟踪：
    只有出现期望的下一条号时才认作条文边界；【条旨】括号一并去除。
    """
    out: dict[str, str] = {}
    matches = list(ART_RE.finditer(text))
    cur = 0
    spans: list[tuple[int, int]] = []  # (start, num)
    for m in matches:
        v = cn_to_int(m.group(1))
        if v is None:
            continue
        # 期望 cur+1；文件开头从 1 开始。引用旧条号（v<=cur）直接跳过
        if (cur == 0 and v == 1) or v == cur + 1:
            spans.append((m.start(), v))
            cur = v
    for i, (pos, v) in enumerate(spans):
        end = spans[i + 1][0] if i + 1 < len(spans) else min(len(text), pos + 1500)
        body = text[pos : end].strip()
        body = ART_RE.sub("", body, count=1).strip()
        body = re.sub(r"^【[^】]{1,12}】", "", body).strip()
        if len(body) >= 15:
            out[str(v)] = body
    return out


def main() -> int:
    import urllib.request

    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    op.addheaders = [("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")]

    index: dict[str, dict] = {}
    fetched = {}
    for name, url in SOURCES:
        try:
            raw = op.open(url, timeout=20).read()
        except Exception as e:  # noqa: BLE001 单源失败不阻断其余
            print(f"[skip] {name}: {type(e).__name__} {str(e)[:60]}")
            continue
        text = strip_html(raw)
        fetched[name] = text
        (OUT / f"fulltext_{name}.txt").write_text(text, encoding="utf-8")
        arts = parse_articles(text)
        if arts:
            index[name] = {"mode": "articles", "url": url, "articles": arts}
            print(f"[ok] {name}: {len(arts)} 条条文")
        else:
            index[name] = {"mode": "fulltext", "url": url, "full": text}
            print(f"[ok] {name}: 全文 {len(text)} 字（无条号结构，供 containment）")
        time.sleep(0.5)

    (OUT / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print("索引写入", OUT / "index.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

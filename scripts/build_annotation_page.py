"""生成真实集盲标页面（自包含 HTML，无需服务器）。

为什么不用 CLI 交互：394 条逐条 input() 需要 2 次回车/条，且不能暂停。
本页面：键盘 1-4 一键定级、自动跳下一条、localStorage 保存进度、
完成后导出 labels_filled.json。

盲标约束：页面**不嵌入系统判定**（不显示高/中/低建议），避免标注被系统结论带偏。

用法：
  python scripts/build_annotation_page.py         # 读 labels.json → annotate.html
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REAL_DIR = ROOT / "data" / "evalset_real"
LABELS = REAL_DIR / "labels.json"
OUT = REAL_DIR / "annotate.html"

TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>契约眼 · 真实合同盲标（394 条）</title>
<style>
  :root{--ink:#0f172a;--sub:#64748b;--line:#e2e8f0;--brand:#4338ca;--ok:#059669;--warn:#d97706;--bad:#dc2626}
  *{box-sizing:border-box}
  body{margin:0;font:14px/1.7 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;color:var(--ink);background:#f8fafc}
  .wrap{max-width:860px;margin:0 auto;padding:16px 18px 40px}
  header{position:sticky;top:0;background:rgba(255,255,255,.94);backdrop-filter:blur(8px);border-bottom:1px solid var(--line);z-index:10}
  .bar{max-width:860px;margin:0 auto;padding:10px 18px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
  .bar b{font-size:15px}
  .progress{flex:1;min-width:140px;height:8px;background:#e2e8f0;border-radius:999px;overflow:hidden}
  .progress > i{display:block;height:100%;background:var(--brand);width:0;transition:.2s}
  .count{font-variant-numeric:tabular-nums;color:var(--sub);font-size:12px}
  .card{background:#fff;border:1px solid var(--line);border-radius:14px;padding:18px 20px;box-shadow:0 1px 2px rgba(15,23,42,.05),0 8px 24px rgba(15,23,42,.04);margin-top:16px}
  .meta{display:flex;gap:10px;flex-wrap:wrap;color:var(--sub);font-size:12px;margin-bottom:10px}
  .tag{background:#f1f5f9;border:1px solid var(--line);border-radius:999px;padding:2px 9px}
  .text{font-size:15px;line-height:1.85;white-space:pre-wrap;max-height:46vh;overflow:auto;padding:12px 14px;background:#fbfcfe;border:1px solid var(--line);border-radius:10px}
  .btns{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:16px}
  .btn{border:1px solid var(--line);background:#fff;border-radius:12px;padding:14px 8px;font-size:15px;font-weight:700;cursor:pointer;transition:.12s;position:relative}
  .btn:hover{border-color:var(--brand);transform:translateY(-1px)}
  .btn .k{position:absolute;top:6px;right:8px;font-size:10px;color:#94a3b8;font-weight:400}
  .btn.high{border-color:#fecaca;color:#991b1b;background:#fef2f2}
  .btn.mid{border-color:#fde68a;color:#92400e;background:#fffbeb}
  .btn.low{border-color:#bfdbfe;color:#1e40af;background:#eff6ff}
  .btn.none{border-color:#a7f3d0;color:#065f46;background:#ecfdf5}
  .btn.active{outline:3px solid var(--brand);outline-offset:1px}
  .row{display:flex;gap:10px;align-items:center;margin-top:12px;flex-wrap:wrap}
  label.sm{font-size:12px;color:var(--sub)}
  select,input[type=text]{border:1px solid var(--line);border-radius:8px;padding:7px 10px;font-size:13px;background:#fff;outline:none}
  select:focus,input:focus{border-color:var(--brand)}
  .foot{display:flex;gap:10px;align-items:center;margin-top:14px;flex-wrap:wrap}
  .ghost{border:1px solid var(--line);background:#fff;border-radius:9px;padding:8px 14px;cursor:pointer;font-size:13px}
  .primary{background:var(--brand);color:#fff;border:none;border-radius:9px;padding:9px 18px;cursor:pointer;font-weight:600;font-size:13px}
  .primary:disabled{opacity:.45;cursor:not-allowed}
  .hint{font-size:12px;color:var(--sub);margin-top:10px}
  .done{text-align:center;padding:60px 20px}
  .done h2{font-size:20px;margin:0 0 8px}
  .toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:#0f172a;color:#fff;padding:9px 16px;border-radius:999px;font-size:13px;opacity:0;transition:.2s;pointer-events:none}
  .toast.show{opacity:1}
  @media(max-width:560px){.btns{grid-template-columns:repeat(2,1fr)}}
</style>
</head>
<body>
<header>
  <div class="bar">
    <b>真实合同盲标</b>
    <div class="progress"><i id="pbar"></i></div>
    <span class="count" id="cnt">0 / 0</span>
  </div>
</header>

<div class="wrap">
  <div id="app"></div>
  <p class="hint">快捷键：<b>1</b>=高 <b>2</b>=中 <b>3</b>=低 <b>4</b>=无 ｜ ←/→ 前后切换 ｜ 进度自动保存在本机浏览器</p>
</div>

<div class="toast" id="toast"></div>

<script>
const DATA = __DATA__;
// v2：2026-11-02 合同文本清洗后重采样，条款索引发生位移；旧键（v1）里的答案
// 会错位映射到新题，必须换键让旧进度失效（答案按 fn#index 存，索引不可混用）。
const KEY = "contractlens.annotate.real.v2";

const LEVELS = [["高","1","high"],["中","2","mid"],["低","3","low"],["无","4","none"]];
const TYPES = ["利率","违约金","担保","隐私","个人信息","还款","缺失","解除","争议","费用","期限","格式条款","免除","其他"];

// 展平为一维题目列表（按合同顺序）
const ITEMS = [];
Object.keys(DATA).sort().forEach(fn => {
  DATA[fn].clauses.forEach((c, j) => {
    ITEMS.push({ fn, ci: j, index: c.index, text: c.text, type: DATA[fn].contract_type, total: DATA[fn].clauses.length });
  });
});

let state = load();
let pos = 0;

function load(){
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) return JSON.parse(raw);
  } catch(e){}
  const init = {answers:{}, pos:0};
  return init;
}
function save(){ try{ localStorage.setItem(KEY, JSON.stringify(state)); }catch(e){} }

function keyOf(it){ return it.fn + "#" + it.index; }

function answeredCount(){
  return ITEMS.filter(it => state.answers[keyOf(it)]).length;
}

function render(){
  const app = document.getElementById("app");
  const total = ITEMS.length;
  const done = answeredCount();
  document.getElementById("cnt").textContent = done + " / " + total;
  document.getElementById("pbar").style.width = (done/total*100).toFixed(1) + "%";

  if (pos >= total || done === total && pos >= total) { app.innerHTML = doneView(done,total); bindDone(); return; }

  const it = ITEMS[pos];
  const a = state.answers[keyOf(it)] || {level: null, type: "其他", note: ""};
  const isHeading = /^[一二三四五六七八九十]+、|^第[一二三四五六七八九十]+条\s*[^。，,；;]{0,12}$/.test(it.text.trim()) && it.text.trim().length <= 16;

  app.innerHTML = `
    <div class="card">
      <div class="meta">
        <span class="tag">${it.fn}</span>
        <span class="tag">${it.type}</span>
        <span class="tag">第 ${pos+1} / ${total} 题</span>
        <span class="tag">文件内序号 ${it.index+1}/${DATA[it.fn].clause_count}</span>
        ${isHeading ? '<span class="tag" style="background:#fef3c7;border-color:#fde68a;color:#92400e">结构项（章节标题，通常无风险）</span>' : ''}
      </div>
      <div class="text">${escapeHtml(it.text)}</div>
      <div class="btns">
        ${LEVELS.map(([lv,k,cls]) => `<button class="btn ${cls} ${a.level===lv?'active':''}" data-lv="${lv}">${lv}<span class="k">${k}</span></button>`).join("")}
      </div>
      <div class="row">
        <label class="sm">风险类型</label>
        <select id="rtype">${TYPES.map(t=>`<option ${a.type===t?'selected':''}>${t}</option>`).join("")}</select>
        <label class="sm">备注（可选）</label>
        <input id="rnote" type="text" placeholder="例如：边界条款、需法务确认" value="${escapeAttr(a.note||"")}" style="flex:1;min-width:140px" />
      </div>
      <div class="foot">
        <button class="ghost" id="prev">← 上一题</button>
        <button class="ghost" id="skip">跳过</button>
        <button class="primary" id="next">下一题 →</button>
        <span class="count" id="left"></span>
      </div>
    </div>`;
  document.getElementById("left").textContent = "剩余 " + (total - done) + " 条未答";
  bindCard(it, a);
}

function bindCard(it, a){
  const k = keyOf(it);
  document.querySelectorAll(".btn").forEach(b => {
    b.onclick = () => choose(it, b.dataset.lv);
  });
  document.getElementById("rtype").onchange = (e) => {
    const cur = state.answers[k] || {level:null,type:"其他",note:""};
    cur.type = e.target.value; state.answers[k]=cur; save();
  };
  document.getElementById("rnote").oninput = (e) => {
    const cur = state.answers[k] || {level:null,type:"其他",note:""};
    cur.note = e.target.value; state.answers[k]=cur; save();
  };
  document.getElementById("prev").onclick = () => { pos = Math.max(0, pos-1); render(); };
  document.getElementById("skip").onclick = () => { pos = Math.min(ITEMS.length, pos+1); render(); };
  document.getElementById("next").onclick = () => { pos = Math.min(ITEMS.length, pos+1); render(); };
}

function choose(it, lv){
  const k = keyOf(it);
  const cur = state.answers[k] || {level:null,type:"其他",note:""};
  cur.level = lv;
  if (!cur.type) cur.type = "其他";
  state.answers[k] = cur;
  if (!cur.note) delete cur.note;
  save();
  toast(lv === "无" ? "已标：无风险" : "已标：" + lv);
  pos = Math.min(ITEMS.length, pos + 1);
  render();
}

document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
  const map = {"1":"高","2":"中","3":"低","4":"无"};
  if (map[e.key]) { const it = ITEMS[pos]; if (it) choose(it, map[e.key]); }
  if (e.key === "ArrowLeft") { pos = Math.max(0,pos-1); render(); }
  if (e.key === "ArrowRight") { pos = Math.min(ITEMS.length,pos+1); render(); }
});

function doneView(done,total){
  return `<div class="card done">
    <h2>${done===total ? "全部标注完成 🎉" : "还有 " + (total-done) + " 条未答"}</h2>
    <p class="count">已答 ${done} / ${total}</p>
    <p style="margin:16px 0 22px;color:#64748b">导出后把文件发回给助手，会合并进 labels.json 并跑校验与评测。</p>
    <button class="primary" id="export" ${done===0?'disabled':''}>导出 labels_filled.json</button>
    <button class="ghost" id="back" style="margin-left:8px">继续标注</button>
  </div>`;
}
function bindDone(){
  const b = document.getElementById("export");
  if (b) b.onclick = exportJson;
  const back = document.getElementById("back");
  if (back) back.onclick = () => { pos = 0; render(); };
}

function exportJson(){
  // 还原为 labels.json 结构（只含被抽样的条款）
  const out = {};
  Object.keys(DATA).forEach(fn => {
    const src = DATA[fn];
    const clauses = src.clauses.map(c => {
      const a = state.answers[fn + "#" + c.index] || {};
      const lv = a.level || null;
      const isRisk = lv ? lv !== "无" : null;
      return {
        index: c.index,
        text: c.text,
        is_risk: isRisk,
        level: lv,
        risk_type: isRisk ? (a.type || "其他") : "",
        rule_id: "",
        annotator_note: a.note || "",
        sampled_by: c.sampled_by || "",
      };
    });
    out[fn] = {
      contract_type: src.contract_type,
      clause_count: src.clause_count,
      risk_clauses: clauses.filter(c => c.is_risk === true).length,
      sampling: src.sampling,
      clauses,
    };
  });
  const blob = new Blob([JSON.stringify(out, null, 2)], {type:"application/json"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "labels_filled.json";
  a.click();
  URL.revokeObjectURL(a.href);
  toast("已导出 labels_filled.json");
}

function toast(msg){
  const t = document.getElementById("toast");
  t.textContent = msg; t.classList.add("show");
  clearTimeout(t._h); t._h = setTimeout(()=>t.classList.remove("show"), 900);
}
function escapeHtml(s){ return (s||"").replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }
function escapeAttr(s){ return (s||"").replace(/"/g,"&quot;"); }

render();
</script>
</body>
</html>
"""


def main() -> int:
    if not LABELS.exists():
        print(f"[x] 缺少 {LABELS}，先跑 sample_real_labels.py")
        return 1
    data = json.loads(LABELS.read_text(encoding="utf-8"))
    n_items = sum(len(v["clauses"]) for v in data.values())
    html = TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    html = html.replace("394 条", f"{n_items} 条")
    OUT.write_text(html, encoding="utf-8")
    print(f"已生成：{OUT}")
    print(f"题目数：{n_items} 条（{len(data)} 份合同）")
    print(f"生成时间：{datetime.now().astimezone():%Y-%m-%d %H:%M:%S %Z}")
    print("打开方式：双击该 HTML，用键盘 1-4 标注，完成后点导出。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

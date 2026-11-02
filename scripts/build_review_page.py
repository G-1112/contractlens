"""生成人工复核页面（AI 预标注 → 人工确认）。

与 build_annotation_page.py 的区别：
  - 显示 AI 预标注的等级与理由（复核场景需要看到判定依据）；
  - 默认过滤「只看已判风险项」，125 条即可覆盖全部 TP/FP 争议点；
  - 回车=同意，1-4=改判；导出后由 merge_annotations.py 合并，
    并把 annotation.method 记为「AI 预标注 + 人工复核」。

产物：data/evalset_real/review.html（自包含，无服务器）
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REAL_DIR = ROOT / "data" / "evalset_real"
LABELS = REAL_DIR / "labels.json"
OUT = REAL_DIR / "review.html"

TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>契约眼 · 标注复核（AI 预标注）</title>
<style>
  :root{--ink:#0f172a;--sub:#64748b;--line:#e2e8f0;--brand:#4338ca;--ok:#059669;--bad:#dc2626;--warn:#d97706}
  *{box-sizing:border-box}
  body{margin:0;font:14px/1.7 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;color:var(--ink);background:#f8fafc}
  header{position:sticky;top:0;z-index:10;background:rgba(255,255,255,.95);backdrop-filter:blur(8px);border-bottom:1px solid var(--line)}
  .bar{max-width:900px;margin:0 auto;padding:10px 18px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
  .progress{flex:1;min-width:120px;height:8px;background:#e2e8f0;border-radius:999px;overflow:hidden}
  .progress > i{display:block;height:100%;background:var(--brand);width:0;transition:.2s}
  .wrap{max-width:900px;margin:0 auto;padding:16px 18px 40px}
  .card{background:#fff;border:1px solid var(--line);border-radius:14px;padding:18px 20px;box-shadow:0 1px 2px rgba(15,23,42,.05)}
  .meta{display:flex;gap:8px;flex-wrap:wrap;color:var(--sub);font-size:12px;margin-bottom:10px}
  .tag{background:#f1f5f9;border:1px solid var(--line);border-radius:999px;padding:2px 9px}
  .text{font-size:15px;line-height:1.85;white-space:pre-wrap;max-height:44vh;overflow:auto;padding:12px 14px;background:#fbfcfe;border:1px solid var(--line);border-radius:10px}
  .judge{margin-top:12px;padding:12px 14px;border-radius:10px;background:#eef2ff;border:1px solid #c7d2fe}
  .judge b{font-size:15px}
  .judge .why{color:#3730a3;font-size:13px;margin-top:4px}
  .btns{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:14px}
  .btn{border:1px solid var(--line);background:#fff;border-radius:12px;padding:12px 6px;font-weight:700;cursor:pointer}
  .btn.high{color:#991b1b;background:#fef2f2;border-color:#fecaca}
  .btn.mid{color:#92400e;background:#fffbeb;border-color:#fde68a}
  .btn.low{color:#1e40af;background:#eff6ff;border-color:#bfdbfe}
  .btn.none{color:#065f46;background:#ecfdf5;border-color:#a7f3d0}
  .btn.active{outline:3px solid var(--brand);outline-offset:1px}
  .foot{display:flex;gap:10px;align-items:center;margin-top:14px;flex-wrap:wrap}
  .primary{background:var(--ok);color:#fff;border:none;border-radius:9px;padding:10px 20px;cursor:pointer;font-weight:700}
  .ghost{border:1px solid var(--line);background:#fff;border-radius:9px;padding:8px 14px;cursor:pointer}
  .hint{font-size:12px;color:var(--sub);margin-top:12px}
  .toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:#0f172a;color:#fff;padding:9px 16px;border-radius:999px;font-size:13px;opacity:0;transition:.2s}
  .toast.show{opacity:1}
  .done{text-align:center;padding:48px 20px}
  .intro{background:#eef2ff;border:1px solid #c7d2fe;border-radius:14px;padding:16px 18px;margin-bottom:14px}
  .intro h2{margin:0 0 8px;font-size:16px}
  .intro p{margin:6px 0;color:#3730a3;font-size:13px;line-height:1.7}
  .intro ol{margin:8px 0 0 18px;padding:0;color:#3730a3;font-size:13px}
  .intro li{margin:4px 0}
  .intro button{margin-top:12px}
</style>
</head>
<body>
<header><div class="bar">
  <b>标注复核</b>
  <label style="font-size:12px;color:#64748b"><input type="checkbox" id="onlyRisk" checked /> 只看风险项</label>
  <div class="progress"><i id="pbar"></i></div>
  <span style="font-size:12px;color:#64748b" id="cnt"></span>
</div></header>
<div class="wrap">
  <div class="intro" id="intro">
    <h2>任务说明（约 15–20 分钟）</h2>
    <p>这是一份「合同风险等级」标注复核。每屏是一条合同条款，系统给出了一个初步判定与理由。<b>你的任务是判断这个判定对不对。</b></p>
    <ol>
      <li><b>回车</b> = 同意该判定，自动进入下一条；</li>
      <li><b>1 / 2 / 3 / 4</b> = 改判为 高 / 中 / 低 / 无；</li>
      <li><b>← →</b> 可前后翻看；进度自动保存在本机浏览器，中途关掉不丢；</li>
      <li>判定口径：<b>高</b>=可能违法或显失公平（如利率超 LPR 四倍、日违约金过高、放弃诉讼权利）；<b>中</b>=明显加重一方责任但未违法（如单方扣划、保证金不退、违约金比例空白）；<b>低</b>=轻微不对等或表述不清；<b>无</b>=常规条款。</li>
    </ol>
    <p>全部过完后点「导出」，把 <code>labels_reviewed.json</code> 发回即可。默认只显示需重点确认的风险项，取消勾选可复核全部条款。</p>
    <button class="ghost" onclick="document.getElementById('intro').style.display='none'">知道了，开始复核</button>
  </div>
  <div id="app"></div>
<p class="hint">回车 = 同意并下一条 ｜ 1=高 2=中 3=低 4=无 改判 ｜ ←/→ 切换 ｜ 进度自动保存</p></div>
<div class="toast" id="toast"></div>
<script>
const DATA = __DATA__;
const KEY = "contractlens.review.real.v1";
const ITEMS = [];
Object.keys(DATA).sort().forEach(fn => {
  DATA[fn].clauses.forEach(c => ITEMS.push({fn, c}));
});
const state = (() => { try { return JSON.parse(localStorage.getItem(KEY)) || {ans:{}, onlyRisk:true}; } catch(e){ return {ans:{}, onlyRisk:true}; } })();
function save(){ try{ localStorage.setItem(KEY, JSON.stringify(state)); }catch(e){} }
let onlyRisk = state.onlyRisk !== false;
let view = [];
function rebuild(){ view = ITEMS.filter(x => !onlyRisk || x.c.is_risk); if (pos >= view.length) pos = 0; }
let pos = 0;
rebuild();

const LV = [["高","1","high"],["中","2","mid"],["低","3","low"],["无","4","none"]];
const key = x => x.fn + "#" + x.c.index;

function render(){
  const total = view.length;
  let done = 0; view.forEach(x => { if (state.ans[key(x)]) done++; });
  document.getElementById("cnt").textContent = done + " / " + total;
  document.getElementById("pbar").style.width = total ? (done/total*100).toFixed(1)+"%" : "0%";
  const app = document.getElementById("app");
  if (!total) { app.innerHTML = '<div class="card done"><h2>没有需要复核的项</h2></div>'; return; }
  if (pos >= total) { app.innerHTML = doneView(done,total); bindDone(); return; }
  const x = view[pos], c = x.c;
  const cur = state.ans[key(x)];
  const level = cur ? cur.level : (c.is_risk ? c.level : "无");
  app.innerHTML = `
  <div class="card">
    <div class="meta">
      <span class="tag">${x.fn}</span>
      <span class="tag">第 ${pos+1} / ${total}</span>
      <span class="tag">条款序号 ${c.index+1}</span>
      ${c.sampled_by ? `<span class="tag">${c.sampled_by === "flagged" ? "系统命中" : "随机抽样"}</span>` : ""}
    </div>
    <div class="text">${esc(c.text)}</div>
    <div class="judge">
      <b>AI 判定：${c.is_risk ? c.level + "风险" : "无风险"}</b>
      <div class="why">${esc(c.annotator_note || "（常规条款，未发现明显不利约定）")}${c.risk_type ? " ｜ 类型：" + c.risk_type : ""}</div>
    </div>
    <div class="btns">
      ${LV.map(([lv,k,cls]) => `<button class="btn ${cls} ${level===lv?'active':''}" data-lv="${lv}">${lv}<span style="font-size:10px;color:#94a3b8"> ${k}</span></button>`).join("")}
    </div>
    <div class="foot">
      <button class="primary" id="ok">同意（回车）</button>
      <button class="ghost" id="prev">← 上一条</button>
      <button class="ghost" id="next">跳过 →</button>
      ${cur ? '<span class="tag" style="color:#059669">已复核</span>' : ''}
    </div>
  </div>`;
  bind(x);
}

function bind(x){
  document.querySelectorAll(".btn").forEach(b => b.onclick = () => decide(x, b.dataset.lv));
  document.getElementById("ok").onclick = () => accept(x);
  document.getElementById("prev").onclick = () => { pos = Math.max(0,pos-1); render(); };
  document.getElementById("next").onclick = () => { pos = Math.min(view.length,pos+1); render(); };
}
function accept(x){ decide(x, x.c.is_risk ? x.c.level : "无", true); }
function decide(x, lv, auto){
  state.ans[key(x)] = {level: lv, agreed: lv === (x.c.is_risk ? x.c.level : "无")};
  save();
  if (!auto) toast("已改判为：" + lv);
  pos = Math.min(view.length, pos+1);
  render();
}
document.addEventListener("keydown", e => {
  if (e.target.tagName === "INPUT") return;
  const m = {"1":"高","2":"中","3":"低","4":"无"};
  if (e.key === "Enter") { const x = view[pos]; if (x) accept(x); e.preventDefault(); }
  if (m[e.key]) { const x = view[pos]; if (x) decide(x, m[e.key]); }
  if (e.key === "ArrowLeft") { pos = Math.max(0,pos-1); render(); }
  if (e.key === "ArrowRight") { pos = Math.min(view.length,pos+1); render(); }
});
document.getElementById("onlyRisk").checked = onlyRisk;
document.getElementById("onlyRisk").onchange = e => { onlyRisk = e.target.checked; state.onlyRisk = onlyRisk; save(); pos = 0; rebuild(); render(); };

function doneView(done,total){
  return `<div class="card done"><h2>${done===total?"复核完成 🎉":"还有 "+(total-done)+" 条未复核"}</h2>
  <p style="color:#64748b">已复核 ${done} / ${total}</p>
  <p style="margin:14px 0 20px;color:#64748b">导出后交给助手合并，标注口径会更新为「AI 预标注 + 人工复核」。</p>
  <button class="primary" id="export">导出 labels_reviewed.json</button>
  <button class="ghost" id="back">继续复核</button></div>`;
}
function bindDone(){
  document.getElementById("export").onclick = exportJson;
  document.getElementById("back").onclick = () => { pos = 0; render(); };
}
function exportJson(){
  const out = {};
  Object.keys(DATA).forEach(fn => {
    const src = DATA[fn]; let reviewed = 0, agreed = 0;
    const clauses = src.clauses.map(c => {
      const a = state.ans[fn + "#" + c.index];
      const lv = a ? a.level : (c.is_risk ? c.level : "无");
      if (a) { reviewed++; if (a.agreed) agreed++; }
      return {...c, is_risk: lv !== "无", level: lv,
              risk_type: lv !== "无" ? (c.risk_type || "其他") : "",
              reviewed: !!a, reviewer_agreed: a ? !!a.agreed : null};
    });
    out[fn] = {...src, clauses, risk_clauses: clauses.filter(c=>c.is_risk).length,
      review: {reviewed, agreed, total: clauses.length,
               method: "AI 预标注 + 人工复核（逐条确认/改判）"}};
  });
  const blob = new Blob([JSON.stringify(out,null,2)],{type:"application/json"});
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
  a.download = "labels_reviewed.json"; a.click(); URL.revokeObjectURL(a.href);
  toast("已导出 labels_reviewed.json");
}
function toast(m){ const t=document.getElementById("toast"); t.textContent=m; t.classList.add("show"); clearTimeout(t._h); t._h=setTimeout(()=>t.classList.remove("show"),900); }
function esc(s){ return (s||"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }
render();
</script>
</body></html>
"""


def main() -> int:
    data = json.loads(LABELS.read_text(encoding="utf-8"))
    n = sum(len(v["clauses"]) for v in data.values())
    risk = sum(1 for v in data.values() for c in v["clauses"] if c.get("is_risk"))
    html = TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    OUT.write_text(html, encoding="utf-8")
    print(f"已生成：{OUT}")
    print(f"条款 {n} 条（其中风险 {risk} 条，默认只复核风险项）")
    print(f"生成时间：{datetime.now().astimezone():%Y-%m-%d %H:%M:%S %Z}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

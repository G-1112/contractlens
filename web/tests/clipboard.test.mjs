import assert from "node:assert";
import { buildRewriteDigest } from "../.build-test/clipboard.js";

let pass = 0, fail = 0;
const it = (name, fn) => { try { fn(); pass++; console.log(`  ✓ ${name}`); }
  catch (e) { fail++; console.log(`  ✗ ${name}\n      ${e.message}`); } };

console.log("buildRewriteDigest 验证\n");

it("空列表返回空串", () => {
  assert.strictEqual(buildRewriteDigest([]), "");
});

it("无改写建议的条款不纳入", () => {
  const out = buildRewriteDigest([{ final_level: "高", clause_id: "C1", clause_text: "x" }]);
  assert.strictEqual(out, "");
});

it("高风险排在前面", () => {
  const out = buildRewriteDigest([
    { final_level: "低", clause_id: "C1", clause_text: "低风险条款", rewrite: "低改写" },
    { final_level: "高", clause_id: "C2", clause_text: "高风险条款", rewrite: "高改写" },
    { final_level: "中", clause_id: "C3", clause_text: "中风险条款", rewrite: "中改写" },
  ]);
  assert.ok(out.indexOf("高改写") < out.indexOf("中改写"), "高应在中之前");
  assert.ok(out.indexOf("中改写") < out.indexOf("低改写"), "中应在低之前");
});

it("仅建议无改写时也能输出（用 suggest 兜底）", () => {
  const out = buildRewriteDigest([
    { final_level: "中", clause_id: "C9", clause_text: "某条款", suggest: "建议这样改" },
  ]);
  assert.ok(out.includes("建议这样改"), out);
});

it("输出包含原文与风险等级", () => {
  const out = buildRewriteDigest([
    { final_level: "高", clause_id: "C1", clause_text: "押金不退", rewrite: "押金应退还", risk_type: "押金强占" },
  ]);
  assert.ok(out.includes("押金不退"), "缺原文");
  assert.ok(out.includes("押金应退还"), "缺改写");
  assert.ok(out.includes("高风险"), "缺风险等级");
});

it("超长原文被截断到 120 字以内", () => {
  const long = "甲".repeat(500);
  const out = buildRewriteDigest([
    { final_level: "高", clause_id: "C1", clause_text: long, rewrite: "短改写" },
  ]);
  const line = out.split("\n").find((l) => l.includes("原文："));
  assert.ok(line.length < 140, `原文行过长: ${line.length}`);
});

it("换行与多余空白被归一化（便于粘贴到聊天窗口）", () => {
  const out = buildRewriteDigest([
    { final_level: "高", clause_id: "C1", clause_text: "第一行\n\n第二行", rewrite: "改写\n内容" },
  ]);
  assert.ok(!/改写\n内容/.test(out), "改写中的换行未归一化");
});

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

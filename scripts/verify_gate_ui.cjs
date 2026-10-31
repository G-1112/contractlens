// 评审口令页 E2E 验证（Playwright + 系统 Edge）
//
// 验证四件事：
//   1. 未带口令时首屏出现口令墙（访问口令 / 进入）
//   2. 错误口令 → 显示「口令不正确」
//   3. 正确口令 → 进入上传页（标题「上传一份合同，逐条看清风险」）
//   4. ?code=xxx 直链 → 跳过口令墙直达上传页（评审打开导出/分享链接的场景）
//
// 用法（项目根目录）：
//   set NODE_PATH=C:\Users\gmk20\.workbuddy\binaries\node\workspace\node_modules
//   node scripts/verify_gate_ui.cjs <base_url> <code>

const { chromium } = require("playwright-core");
const path = require("node:path");
const fs = require("node:fs");

const BASE = process.argv[2] || "http://127.0.0.1:8000";
const CODE = process.argv[3] || "";
const OUT = path.join(process.cwd(), "docs", "screenshots");
fs.mkdirSync(OUT, { recursive: true });

const results = [];
const check = (name, pass, detail) => {
  results.push({ name, pass, detail });
  console.log(`${pass ? "PASS" : "FAIL"}  ${name}  ${detail || ""}`);
};
// isVisible() 不等待（timeout 被忽略），统一用 waitFor 做「有限时等待的可见性判断」
const waitVisible = async (locator, timeout = 15000) => {
  try {
    await locator.waitFor({ state: "visible", timeout });
    return true;
  } catch {
    return false;
  }
};

(async () => {
  const browser = await chromium.launch({ channel: "msedge", headless: true });

  // ---- 场景 1/2/3：口令墙 → 错误 → 正确 ----
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 } });
  const page = await ctx.newPage();
  await page.goto(BASE, { waitUntil: "networkidle" });

  const gateVisible = await waitVisible(page.getByText("访问口令", { exact: false }).first(), 20000);
  check("首屏出现口令墙", gateVisible);
  if (gateVisible) await page.screenshot({ path: path.join(OUT, "gate-locked.png") });

  if (gateVisible && CODE) {
    await page.locator("#review-code").fill("WRONG-CODE");
    await page.getByRole("button", { name: "进入" }).click();
    const errShown = await waitVisible(page.getByText("口令不正确", { exact: false }).first(), 15000);
    check("错误口令提示", errShown);

    await page.locator("#review-code").fill(CODE);
    await page.getByRole("button", { name: "进入" }).click();
    const unlocked = await waitVisible(page.getByText("上传一份合同", { exact: false }).first(), 20000);
    check("正确口令进入上传页", unlocked);
    if (unlocked) {
      await page.screenshot({ path: path.join(OUT, "gate-unlocked.png") });
      const stored = await page.evaluate(() => localStorage.getItem("contractlens.reviewCode"));
      check("口令已持久化到 localStorage", stored === CODE, `stored=${stored ? "yes" : "no"}`);
    }
  }
  await ctx.close();

  // ---- 场景 4：?code= 直链 ----
  if (CODE) {
    const ctx2 = await browser.newContext({ viewport: { width: 1280, height: 860 } });
    const page2 = await ctx2.newPage();
    await page2.goto(`${BASE}/?code=${encodeURIComponent(CODE)}`, { waitUntil: "networkidle" });
    const direct = await waitVisible(page2.getByText("上传一份合同", { exact: false }).first(), 20000);
    check("?code= 直链直达上传页", direct);
    const cleaned = page2.url().includes("code=") === false;
    check("地址栏已清理口令参数", cleaned, page2.url());
    await ctx2.close();
  }

  await browser.close();

  const failed = results.filter((r) => !r.pass);
  console.log(`\n小计 ${results.length} 项，通过 ${results.length - failed.length}，失败 ${failed.length}`);
  process.exit(failed.length ? 1 : 0);
})();

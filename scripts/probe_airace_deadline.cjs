/**
 * 抓取大赛官网首页的真实报名截止时间。
 *
 * 背景：本地文件口径为 11-10 24:00（调整通知 + 校赛通知），
 * 但官网首页倒计时 JS 兜底值是 2026-10-31T23:59:59+08:00，
 * 且优先读服务端 signEndDt。二者矛盾，必须实测确认。
 *
 * 做法：用系统 Edge 打开首页，监听所有响应体里含 signEnd 的请求，
 * 同时读取渲染后的倒计时文本。不做任何登录操作。
 */
const { chromium } = require("playwright-core");

(async () => {
  const browser = await chromium.launch({ channel: "msedge", headless: true });
  const page = await browser.newPage();

  const hits = [];
  page.on("response", async (resp) => {
    try {
      const ct = resp.headers()["content-type"] || "";
      if (!ct.includes("json")) return;
      const body = await resp.text();
      if (body.includes("signEnd")) {
        hits.push({ url: resp.url(), status: resp.status(), body: body.slice(0, 1200) });
      }
    } catch (e) {
      /* 忽略已销毁的响应 */
    }
  });

  await page.goto("https://www.ai-race.com.cn/", { waitUntil: "networkidle", timeout: 60000 });
  await page.waitForTimeout(4000);

  // 抓倒计时区域文本
  const countdown = await page.evaluate(() => {
    const out = [];
    document.querySelectorAll("div,span,p").forEach((el) => {
      const t = (el.textContent || "").trim();
      if (t.length < 90 && /距离报名截止|报名截止/.test(t)) out.push(t.replace(/\s+/g, " "));
    });
    return [...new Set(out)].slice(0, 6);
  });

  console.log("=== 倒计时区域文本 ===");
  console.log(JSON.stringify(countdown, null, 2));

  console.log("\n=== 含 signEnd 的接口响应 ===");
  if (!hits.length) console.log("（未捕获到，可能走了缓存或字段名不同）");
  hits.forEach((h) => console.log(h.url, h.status, "\n", h.body, "\n---"));

  await browser.close();
})();

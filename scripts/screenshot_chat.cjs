// 前端真实界面截图：上传借款合同 → 分析 → 追问对话 → 截图
// 用途：PPT P16 对话界面页素材（真实运行截图，非设计稿模拟）
//
// 运行前提：后端 8000 + 前端 5173 已启动
// 用法（在项目根目录）：
//   set NODE_PATH=C:\Users\gmk20\.workbuddy\binaries\node\workspace\node_modules
//   node scripts/screenshot_chat.cjs
// 依赖：playwright-core（走系统 Edge，无需下载浏览器）

const { chromium } = require("playwright-core");
const path = require("node:path");
const fs = require("node:fs");

const ROOT = process.cwd();
const SAMPLE = path.join(ROOT, "data", "samples", "借款合同样本.docx");
const OUT = path.join(ROOT, "docs", "screenshots");
fs.mkdirSync(OUT, { recursive: true });

(async () => {
  const browser = await chromium.launch({ channel: "msedge", headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  page.on("console", (msg) => console.log(`[console.${msg.type()}]`, msg.text()));
  page.on("pageerror", (err) => console.log("[pageerror]", err.message));
  page.on("requestfailed", (req) =>
    console.log("[requestfailed]", req.url(), req.failure()?.errorText),
  );

  try {
    await page.goto("http://127.0.0.1:5173", { waitUntil: "networkidle" });

    // 1) 选择「借款合同」类型卡片
    await page.getByText("借款合同", { exact: true }).click();

    // 2) 上传样本文件
    await page.setInputFiles('input[type="file"]', SAMPLE);

    // 3) 开始审查（上传成功后会自动进入 SSE 分析）
    await page.getByRole("button", { name: "开始审查" }).click();

    // 4) 等待报告页渲染完成；超时则 dump 页面状态便于诊断
    try {
      await page.getByText("审查报告", { exact: true }).waitFor({ timeout: 120000 });
    } catch {
      const bodyText = await page.locator("body").innerText();
      console.log("=== 页面当前文本（前 1500 字） ===");
      console.log(bodyText.slice(0, 1500));
      await page.screenshot({ path: path.join(OUT, "debug-stuck.png") });
      throw new Error("报告页未出现，已输出页面文本与 debug-stuck.png");
    }
    await page.waitForTimeout(1500);

    // 报告页整页截图（PPT 审查报告页备用素材）
    await page.screenshot({ path: path.join(OUT, "report-full.png"), fullPage: true });
    console.log("saved report-full.png");

    // 5) 滚到追问面板，发一条真实问题
    const panel = page.getByTestId("chat-panel");
    await panel.scrollIntoViewIfNeeded();
    await page
      .getByPlaceholder(/针对这份合同继续提问/)
      .fill("第4条约定的年利率是否合法？请引用具体法条说明");
    await page.getByRole("button", { name: "发送" }).click();

    // 6) 等回答与引用来源渲染出来
    await page.getByText("引用来源（可追溯）").waitFor({ timeout: 60000 });
    await page.waitForTimeout(600);

    // 消息区滚到接近顶部（留 48px），让「用户提问 + 回答 + 全部引用来源」都入镜，
    // 避免默认滚到底丢掉问题、滚到顶又把末尾来源 chip 裁掉一半
    await panel
      .locator(".overflow-y-auto")
      .first()
      .evaluate((el) => {
        el.scrollTop = 48;
      });
    await page.waitForTimeout(300);

    // 7) 对话面板单独截图（P16 主素材）
    await panel.screenshot({ path: path.join(OUT, "chat-panel.png") });
    console.log("saved chat-panel.png");

    // 8) 视口截图：面板在报告页上下文中的样子（P16 备用素材）
    await page.screenshot({ path: path.join(OUT, "chat-in-context.png") });
    console.log("saved chat-in-context.png");

    console.log("OK:", fs.readdirSync(OUT).join(", "));
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error("FAILED:", e.message || e);
  process.exit(1);
});

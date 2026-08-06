// 真实环境验证（手动）：加载扩展 → 真实 daemon（127.0.0.1:8765）→ 真实 B 站页面。
//
// 前置条件：
//   1. 本地 b2text daemon 已启动（新代码）且模型已加载
//   2. 有 B 站 cookie（daemon 用）
// 运行（会打开受控 Chrome 窗口）：
//   node chrome-extension/e2e/real-check.js [BV号或URL]
"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execSync } = require("node:child_process");

function loadPlaywright() {
  try {
    return require("playwright");
  } catch {
    const root = execSync("npm root -g").toString().trim();
    return require(path.join(root, "playwright"));
  }
}

function resolveChromiumPath() {
  if (process.env.PLAYWRIGHT_CHROMIUM_PATH) {
    return process.env.PLAYWRIGHT_CHROMIUM_PATH;
  }
  const candidates = [
    path.join(os.homedir(), "Library/Caches/ms-playwright/chromium-1148/chrome-mac/Chromium.app/Contents/MacOS/Chromium"),
    path.join(os.homedir(), "Library/Caches/ms-playwright/chromium-1148/chrome-mac-arm64/Chromium.app/Contents/MacOS/Chromium"),
  ];
  return candidates.find((p) => fs.existsSync(p)) || undefined;
}

async function main() {
  const input = process.argv[2] || "https://www.bilibili.com/video/BV1mCGA6QEES/";
  const EXT_PATH = path.join(__dirname, "..");
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), "b2text-real-"));
  const { chromium } = loadPlaywright();
  const context = await chromium.launchPersistentContext(userDataDir, {
    headless: false,
    executablePath: resolveChromiumPath(),
    args: [
      `--disable-extensions-except=${EXT_PATH}`,
      `--load-extension=${EXT_PATH}`,
      "--no-first-run",
    ],
  });

  try {
    let sw = context.serviceWorkers().find((w) => w.url().includes("chrome-extension://"));
    if (!sw) sw = await context.waitForEvent("serviceworker", { timeout: 15000 });
    const extId = new URL(sw.url()).host;
    console.log("[real] 扩展 id:", extId);

    const optionsPage = await context.newPage();
    await optionsPage.goto(`chrome-extension://${extId}/options/options.html`);
    await optionsPage.waitForSelector("#backendUrl");
    await optionsPage.fill("#backendUrl", "http://127.0.0.1:8765");
    await optionsPage.click("#save");
    await optionsPage.waitForFunction(
      () => document.getElementById("saved").textContent.includes("已保存"),
      null,
      { timeout: 5000 }
    );
    console.log("[real] 后端地址已设为 127.0.0.1:8765");

    const page = await context.newPage();
    page.on("pageerror", (err) => console.log("[real] PAGEERROR:", err.message));
    page.on("console", (msg) => {
      if (msg.type() === "error" || msg.type() === "warning") {
        console.log(`[real] console(${msg.type()}):`, msg.text().slice(0, 300));
      }
    });
    console.log("[real] 打开:", input);
    await page.goto(input, { timeout: 90000, waitUntil: "domcontentloaded" });
    await page.waitForSelector(".b2text-panel", { timeout: 120000 });
    console.log("[real] 文字面板已出现，手动点击「开始解析」…");
    await page.click('button[data-act="start"]');
    console.log("[real] 等待解析结果…");
    await page.waitForFunction(
      () => {
        const s = document.querySelector(".b2text-status");
        return s && (s.textContent.includes("完成") || s.textContent.includes("失败"));
      },
      null,
      { timeout: 600000 }
    );

    const status = await page.evaluate(
      () => document.querySelector(".b2text-status").textContent
    );
    const err = await page.evaluate(() => {
      const box = document.querySelector(".b2text-error");
      if (!box || box.hidden) return null;
      const t = box.querySelector(".b2text-error-text");
      return t ? t.textContent : null;
    });
    const lines = await page.locator(".b2text-line").count();
    console.log("[real] 状态:", status);
    console.log("[real] 句数:", lines);
    if (err) console.log("[real] 错误:", err);
    if (!status.includes("完成") || lines === 0) {
      console.error("[real] 结果：失败");
      process.exitCode = 1;
    } else {
      console.log("[real] 结果：成功 ✓");
    }
  } finally {
    await context.close();
    fs.rmSync(userDataDir, { recursive: true, force: true });
  }
}

main().catch((e) => {
  console.error("[real] 失败：", e);
  process.exit(1);
});

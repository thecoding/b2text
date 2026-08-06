// b2text 扩展端到端测试（Playwright）
//
// 运行（需要 GUI，会弹出一个 Chrome 窗口）：
//   node --test chrome-extension/e2e/extension-e2e.test.js
//
// 流程：
//   1. 用 Playwright 加载真实扩展（headful）
//   2. 打开扩展设置页，把后端地址指向本地 mock 服务
//   3. 用路由拦截 bilibili.com，返回一个假播放页（含 <video>）
//   4. 验证：自动提交解析 → 渲染时间线 → 快捷键/点击跳句 →
//      选句循环播放 → 文字层关闭与悬浮入口重开 → 遮挡层开关 →
//      失败场景完整错误展示
"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const http = require("node:http");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync, execSync } = require("node:child_process");

function loadPlaywright() {
  try {
    return require("playwright");
  } catch {
    const root = execSync("npm root -g").toString().trim();
    return require(path.join(root, "playwright"));
  }
}

const { chromium } = loadPlaywright();
const EXT_PATH = path.join(__dirname, "..");

/** 解析可用的 Chromium 二进制：优先环境变量，其次本地 playwright 缓存 */
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

const SEGMENTS = [
  { start: 5, end: 8, speaker: "Speaker_1", text: "大家好欢迎来到本期节目" },
  { start: 12, end: 18, speaker: "Speaker_2", text: "今天我们来聊一聊最近比较火的一个话题" },
  { start: 22, end: 30, speaker: "Speaker_1", text: "对这个话题我有几个看法" },
];

const FIXTURE_HTML = `<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>测试视频</title></head>
<body>
  <div class="bpx-player-container">
    <video controls src="/media/sample.webm" style="width:640px;height:360px"></video>
  </div>
</body>
</html>`;

/** mock b2text daemon：POST /transcribe、GET /tasks/{id}、GET /tasks/{id}/segments */
function startMockDaemon(segments) {
  const tasks = new Map();
  const submits = new Map();
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://127.0.0.1");
    const cors = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
    };
    if (req.method === "OPTIONS") {
      res.writeHead(204, cors);
      res.end();
      return;
    }
    res.setHeader("Content-Type", "application/json");
    Object.entries(cors).forEach(([k, v]) => res.setHeader(k, v));

    if (req.method === "POST" && url.pathname === "/transcribe") {
      let body = "";
      req.on("data", (c) => (body += c));
      req.on("end", () => {
        const data = JSON.parse(body || "{}");
        const bvid = data.id || "";
        const taskId = `task-${tasks.size + 1}`;
        const count = (submits.get(bvid) || 0) + 1;
        submits.set(bvid, count);
        // BV1RETRY：第一次失败、第二次成功（验证失败任务不残留缓存）
        const failed =
          /^BV1FAIL/i.test(bvid) || (/^BV1RETRY/i.test(bvid) && count === 1);
        tasks.set(taskId, {
          taskId,
          bvid,
          status: failed ? "failed" : "done",
          error: failed
            ? "BiliAPIError: B站 API 错误：code=-404, message='啥都木有'"
            : null,
        });
        res.end(JSON.stringify({ task_id: taskId }));
      });
      return;
    }

    const m = url.pathname.match(/^\/tasks\/([^/]+)(\/segments)?$/);
    if (req.method === "GET" && m) {
      const task = tasks.get(m[1]);
      if (!task) {
        res.statusCode = 404;
        res.end(JSON.stringify({ detail: "not found" }));
        return;
      }
      if (m[2]) {
        if (task.status === "done") {
          res.end(JSON.stringify({ status: "done", segments, duration: 60 }));
        } else {
          res.end(JSON.stringify({ status: task.status, segments: [], duration: 0 }));
        }
        return;
      }
      res.end(
        JSON.stringify({
          id: task.taskId,
          bvid: task.bvid,
          status: task.status,
          error: task.error,
        })
      );
      return;
    }

    res.statusCode = 404;
    res.end(JSON.stringify({ detail: "not found" }));
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve(server)));
}

/** 生成 60s 无声 webm，保证 <video> 有可 seek 的时长 */
function ensureSampleMedia() {
  const out = path.join(os.tmpdir(), `b2text-e2e-sample-${process.pid}.webm`);
  if (!fs.existsSync(out)) {
    execFileSync(
      "ffmpeg",
      [
        "-y", "-f", "lavfi", "-i", "color=c=black:s=160x90:d=60",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
        "-shortest", "-c:v", "libvpx", "-b:v", "64k",
        "-g", "25", "-keyint_min", "25",
        "-c:a", "libopus", out,
      ],
      { stdio: "ignore" }
    );
  }
  return out;
}

async function runE2E() {
  const log = (msg) => console.log(`[e2e] ${msg}`);
  log("启动 mock 后端与生成测试视频…");
  const mockServer = await startMockDaemon(SEGMENTS);
  const mockPort = mockServer.address().port;
  const mediaPath = ensureSampleMedia();
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), "b2text-e2e-profile-"));
  log(`启动 Chromium（headful, port=${mockPort}）…`);
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
    log("注册 bilibili.com 路由…");
    // 拦截 B 站：播放页返回 fixture，媒体文件返回生成的 webm
    await context.route("https://www.bilibili.com/**", (route) => {
      const url = route.request().url();
      if (url.includes("/media/sample.webm")) {
        const data = fs.readFileSync(mediaPath);
        const range = route.request().headers()["range"];
        if (range) {
          const m = range.match(/bytes=(\d+)-(\d*)/);
          const start = m ? parseInt(m[1], 10) : 0;
          const end = m && m[2] ? parseInt(m[2], 10) : data.length - 1;
          return route.fulfill({
            status: 206,
            headers: {
              "Content-Type": "video/webm",
              "Accept-Ranges": "bytes",
              "Content-Range": `bytes ${start}-${end}/${data.length}`,
              "Content-Length": String(end - start + 1),
            },
            body: data.subarray(start, end + 1),
          });
        }
        return route.fulfill({
          status: 200,
          contentType: "video/webm",
          headers: { "Accept-Ranges": "bytes" },
          body: data,
        });
      }
      if (/\/video\/BV[\w]+/.test(url)) {
        return route.fulfill({
          status: 200,
          contentType: "text/html; charset=utf-8",
          body: FIXTURE_HTML,
        });
      }
      return route.fulfill({ status: 404, body: "nope" });
    });

    // 扩展 service worker + 扩展 id
    log("等待扩展 service worker…");
    let sw = context.serviceWorkers().find((w) => w.url().includes("chrome-extension://"));
    if (!sw) sw = await context.waitForEvent("serviceworker", { timeout: 15000 });
    const extId = new URL(sw.url()).host;
    assert.ok(extId.length >= 16, `extension id: ${extId}`);
    log(`扩展 id: ${extId}`);

    // 1. 设置页：把后端指向 mock 服务
    log("配置设置页后端地址…");
    const optionsPage = await context.newPage();
    await optionsPage.goto(`chrome-extension://${extId}/options/options.html`);
    await optionsPage.waitForSelector("#backendUrl");
    await optionsPage.fill("#backendUrl", `http://127.0.0.1:${mockPort}`);
    await optionsPage.click("#save");
    await optionsPage.waitForFunction(
      () => document.getElementById("saved").textContent.includes("已保存"),
      null,
      { timeout: 5000 }
    );
    log("设置已保存");

    // 2. 打开假 B 站播放页（BV 号带小写，验证大小写不被破坏）
    log("打开假 B 站播放页…");
    const page = await context.newPage();
    const pageErrors = [];
    const consoleErrors = [];
    page.on("pageerror", (err) => pageErrors.push(err.message));
    page.on("console", (msg) => {
      if (msg.type() === "error" || msg.type() === "warning") {
        consoleErrors.push(msg.text());
      }
    });
    await page.goto("https://www.bilibili.com/video/BV12sbnzZEMD");
    log("等待文字面板出现…");
    await page.waitForSelector(".b2text-panel", { timeout: 15000 });
    log("手动点击「开始解析」…");
    await page.click('button[data-act="start"]');
    log("等待解析完成…");
    await page.waitForFunction(
      () => {
        const s = document.querySelector(".b2text-status");
        return s && s.textContent.includes("完成");
      },
      null,
      { timeout: 15000 }
    );
    log("渲染时间线断言…");
    const lineCount = await page.locator(".b2text-line").count();
    assert.equal(lineCount, SEGMENTS.length);
    const firstLine = await page.locator(".b2text-line").first().textContent();
    assert.ok(firstLine.includes("大家好欢迎来到本期节目"));

    const sendCommand = (command) =>
      sw.evaluate(
        (cmd) =>
          new Promise((resolve) => {
            chrome.tabs.query({}, (tabs) => {
              for (const t of tabs) {
                chrome.tabs.sendMessage(t.id, { type: "COMMAND", command: cmd }, () => {});
              }
              resolve();
            });
          }),
        command
      );

    const videoTime = () => page.evaluate(() => document.querySelector("video").currentTime);

    // 3. 跳句（点击文字行 / 快捷键命令）
    log("测试点击文字行跳句…");
    await page.locator(".b2text-line", { hasText: "今天我们来聊一聊" }).click();
    await page.waitForFunction(
      () => Math.abs(document.querySelector("video").currentTime - 12) < 0.3,
      null,
      { timeout: 8000 }
    );
    assert.ok(Math.abs((await videoTime()) - 12) < 0.3);
    assert.equal(await page.locator(".b2text-line.active").count(), 1);

    log("测试命令跳句（next-sentence）…");
    await sendCommand("next-sentence");
    await page.waitForFunction(
      () => Math.abs(document.querySelector("video").currentTime - 22) < 0.3,
      null,
      { timeout: 8000 }
    );
    assert.ok(Math.abs((await videoTime()) - 22) < 0.3);

    log("测试命令跳句（prev-sentence）…");
    await page.evaluate(() => {
      document.querySelector("video").currentTime = 20; // 间隙
    });
    await sendCommand("prev-sentence");
    await page.waitForFunction(
      () => Math.abs(document.querySelector("video").currentTime - 12) < 0.3,
      null,
      { timeout: 8000 }
    );
    assert.ok(Math.abs((await videoTime()) - 12) < 0.3);
    await page.evaluate(() => {
      document.querySelector("video").currentTime = 15; // 第二句中间
    });
    await sendCommand("prev-sentence");
    await page.waitForFunction(
      () => Math.abs(document.querySelector("video").currentTime - 5) < 0.3,
      null,
      { timeout: 8000 }
    );
    assert.ok(Math.abs((await videoTime()) - 5) < 0.3);

    // 3.5 选句循环播放：勾选两句 → 开循环 → 句尾跳下一句 → 最后一句回第一句
    log("测试选句循环播放…");
    const checkLine = (text) =>
      page
        .locator(".b2text-line", { hasText: text })
        .locator("input.b2text-check")
        .click();
    await checkLine("大家好欢迎来到本期节目");
    await checkLine("今天我们来聊一聊");
    assert.equal(await page.locator(".b2text-line.selected").count(), 2);
    await page.click('button[data-act="loop"]');
    await page.waitForFunction(
      () => document.querySelector('button[data-act="loop"]').classList.contains("on"),
      null,
      { timeout: 5000 }
    );
    const statusText = await page.locator(".b2text-status").textContent();
    assert.ok(statusText.includes("循环中（2 句）"));

    const fireTimeupdate = (t) =>
      page.evaluate((time) => {
        const v = document.querySelector("video");
        v.currentTime = time;
        v.dispatchEvent(new Event("timeupdate"));
      }, t);
    // 第一句（5~8s）结束后应跳到第二句（12s）
    await fireTimeupdate(8.05);
    await page.waitForFunction(
      () => Math.abs(document.querySelector("video").currentTime - 12) < 0.3,
      null,
      { timeout: 8000 }
    );
    // 第二句（12~18s）结束后应回到第一句（5s）
    await fireTimeupdate(18.05);
    await page.waitForFunction(
      () => Math.abs(document.querySelector("video").currentTime - 5) < 0.3,
      null,
      { timeout: 8000 }
    );
    log("选句循环播放通过");

    // 4. 遮挡层开关 + 悬浮入口重开
    log("测试遮挡层…");
    await sendCommand("toggle-overlay");
    await page.waitForSelector(".b2text-overlay", { state: "visible", timeout: 8000 });

    // 默认是视频底部字幕条（不是整块遮挡），右下角可缩放且底边锚定
    const overlayBox = await page.locator(".b2text-overlay").boundingBox();
    const videoBox = await page.locator("video").boundingBox();
    assert.ok(overlayBox && videoBox);
    assert.ok(
      overlayBox.height < videoBox.height * 0.5,
      `遮挡层应只占视频底部一部分（${overlayBox.height}/${videoBox.height}）`
    );
    const handleBox = await page.locator(".b2text-overlay-resize").boundingBox();
    assert.ok(handleBox, "右下角应有缩放手柄");
    const h0 = overlayBox.height;
    await page.mouse.move(handleBox.x + handleBox.width / 2, handleBox.y + handleBox.height / 2);
    await page.mouse.down();
    await page.mouse.move(
      handleBox.x + handleBox.width / 2 + 20,
      handleBox.y + handleBox.height / 2 + 40
    );
    await page.mouse.up();
    const overlayBox2 = await page.locator(".b2text-overlay").boundingBox();
    assert.ok(overlayBox2.height > h0, "拖动右下角应增大遮挡层高度");
    assert.ok(
      Math.abs(
        overlayBox2.y + overlayBox2.height - (overlayBox.y + overlayBox.height)
      ) < 2,
      "缩放时遮挡层底边应锚定不动"
    );

    // 整个遮挡条可上下平移（按住条身拖动）
    const barBefore = await page.locator(".b2text-overlay").boundingBox();
    const barCx = barBefore.x + barBefore.width / 2;
    const barCy = barBefore.y + barBefore.height / 2;
    await page.mouse.move(barCx, barCy);
    await page.mouse.down();
    await page.mouse.move(barCx, barCy - 40);
    await page.mouse.up();
    const barAfter = await page.locator(".b2text-overlay").boundingBox();
    assert.ok(
      Math.abs(barAfter.y - (barBefore.y - 40)) < 3,
      `上下平移距离应为 -40，实际 ${barAfter.y - barBefore.y}`
    );

    await sendCommand("toggle-overlay");
    await page.waitForSelector(".b2text-overlay", { state: "hidden", timeout: 8000 });
    await page.locator(".b2text-launcher", { hasText: "遮挡" }).click();
    await page.waitForSelector(".b2text-overlay", { state: "visible", timeout: 8000 });

    // 5. 文字层关闭 → 悬浮「转写」入口重开
    log("测试文字层开关…");
    await page.click('.b2text-panel-header button[data-act="close"]');
    await page.waitForSelector(".b2text-panel", { state: "hidden", timeout: 8000 });
    await page.locator(".b2text-launcher", { hasText: "转写" }).click();
    await page.waitForSelector(".b2text-panel", { state: "visible", timeout: 8000 });

    // 6. 失败场景：错误完整显示 + 复制按钮存在
    log("测试失败场景…");
    const errPage = await context.newPage();
    await errPage.goto("https://www.bilibili.com/video/BV1FAILxxx");
    await errPage.waitForSelector(".b2text-panel", { timeout: 15000 });
    await errPage.click('button[data-act="start"]');
    await errPage.waitForSelector(".b2text-error", { state: "visible", timeout: 15000 });
    const errText = await errPage.locator(".b2text-error-text").textContent();
    assert.ok(errText.includes("-404"), `error text: ${errText}`);
    assert.ok(errText.includes("啥都木有"), `error text: ${errText}`);
    assert.ok(await errPage.locator('[data-act="copy-error"]').isVisible());

    // 7. 失败任务不残留：重新打开页面应自动重新解析并成功
    log("测试失败后重开自动重新解析…");
    const retryPage = await context.newPage();
    await retryPage.goto("https://www.bilibili.com/video/BV1RETRYxxx");
    await retryPage.waitForSelector(".b2text-panel", { timeout: 15000 });
    await retryPage.click('button[data-act="start"]');
    await retryPage.waitForSelector(".b2text-error", { state: "visible", timeout: 15000 });
    await retryPage.reload();
    await retryPage.waitForSelector(".b2text-panel", { timeout: 15000 });
    await retryPage.click('button[data-act="start"]');
    await retryPage.waitForFunction(
      () => {
        const s = document.querySelector(".b2text-status");
        return s && s.textContent.includes("完成");
      },
      null,
      { timeout: 20000 }
    );
    assert.equal(await retryPage.locator(".b2text-line").count(), SEGMENTS.length);
    // 加载/运行过程中 content script 不应有报错（回归：content.js 141 行问题）
    assert.ok(pageErrors.length === 0, `页面错误: ${pageErrors.join("; ")}`);
    assert.ok(
      !consoleErrors.some((t) => t.includes("content.js")),
      `content.js 控制台错误: ${consoleErrors.join("; ")}`
    );
    log("全部通过 ✓");
  } finally {
    log("清理…");
    try {
      if (context) await context.close();
    } catch {
      // 忽略关闭失败
    }
    try {
      await new Promise((resolve) => mockServer.close(resolve));
    } catch {
      // 忽略关闭失败
    }
    fs.rmSync(userDataDir, { recursive: true, force: true });
    fs.rmSync(mediaPath, { force: true });
  }
}

if (require.main === module) {
  runE2E().catch((err) => {
    console.error("[e2e] 失败：", err);
    process.exit(1);
  });
} else {
  test("扩展端到端：解析、跳句、文字层/遮挡层、错误展示", { timeout: 150000 }, runE2E);
}

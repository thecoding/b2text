# 扩展端到端测试（Playwright）

用 Playwright 加载真实扩展，配合 mock 的 B 站播放页和 mock 后端，
验证完整流程：手动点击「开始解析」→ 时间线渲染 → 跳句 → 文字层/遮挡层开关 →
失败场景的错误完整展示与重试。

## 前置条件

- 全局或可解析的 `playwright`（`npm i -g playwright`）+ 浏览器二进制
  （`npx playwright install chromium`）
- `ffmpeg`（用于生成测试用无声视频）
- 需要 GUI：测试会弹出一个受控 Chrome 窗口（扩展只能在 headful 下加载）

## 运行

```bash
node --test chrome-extension/e2e/extension-e2e.test.js
```

不需要启动 b2text daemon——测试自带 mock 后端，也不会访问真实 B 站。

## 真实环境验证（可选）

```bash
# 需要：daemon 已启动（新代码）+ B 站 cookie
node chrome-extension/e2e/real-check.js "https://www.bilibili.com/video/BV1mCGA6QEES/"
```

加载真实扩展 → 指向本地 daemon → 打开真实 B 站页面，等待解析完成并打印句数。

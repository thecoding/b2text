# b2text Chrome 扩展

在看 B 站视频时，把当前视频（BV 号）发给本地 b2text daemon 转写，
拿回带时间戳的句子时间线，用快捷键在句子之间跳转，并可选显示
转写文字层或半透明遮挡层。

## 目录

```
chrome-extension/
├── manifest.json     # MV3 清单：权限 / content script / 快捷键
├── background.js     # service worker：提交任务、轮询、缓存时间线、转发命令
├── content.js        # 注入播放页：读 BV 号、控制 <video> 跳句、渲染文字/遮挡层
├── content.css       # 文字层 / 遮挡层样式
├── lib/seek.js       # 上一句/下一句的二分查找（纯逻辑，可单测）
├── lib/loop.js       # 选句循环的跳转目标计算（纯逻辑，可单测）
├── popup/            # 工具栏弹窗：状态、提交/重试
├── options/          # 设置页：后端地址、显示开关、样式
├── tests/            # Node 单测（node --test tests/seek.test.js）
└── docs/PLAN.md      # 实施计划与 API 契约
```

## 前置条件

1. 本地 b2text daemon 已启动（`b2text serve start`，默认 `127.0.0.1:8765`；
   后端地址可在扩展设置页修改）
2. daemon 版本需包含扩展所需的 segments API（`GET /tasks/{id}/segments` +
   CORS；本仓库已实现）

## 安装（开发）

1. Chrome 打开 `chrome://extensions`
2. 开启「开发者模式」
3. 「加载已解压的扩展程序」→ 选择本目录
4. 快捷键默认：`Ctrl+Shift+→` 下一句、`Ctrl+Shift+←` 上一句、
   `Ctrl+Shift+T` 文字层、`Ctrl+Shift+O` 遮挡层、`Ctrl+Shift+L` 循环播放
   选中句（macOS 用 Control 键，即 `^+Shift`；可在
   chrome://extensions/shortcuts 修改）。注：Chrome 限制单个扩展最多 4 个
   `chrome.commands` 默认快捷键，前四个走系统快捷键，循环快捷键由扩展内
   按键监听实现（在播放页内按 `Ctrl/^+Shift+L` 即可）。

## 当前状态

已实现：打开播放页后手动点击「开始解析」提交转写、轮询时间线、快捷键跳句、
可拖拽/调字号的文字层、可拖拽/缩放的遮挡层（颜色/透明度可配）、设置页与弹窗。
遮挡层默认是视频底部的一条字幕遮挡条（高度约 1/4），可拖拽移动、右下角
拖拽调整大小（底边固定）。转写完成后勾选一句或多句并点「循环」（或按
`Ctrl/^+Shift+L`），播放到句尾自动跳到下一选中句，最后一句结束回到第一句；
未选句时开启循环默认选中当前播放句。

## 测试

```bash
node --test tests/*.test.js      # 跳句/循环纯逻辑 + BV 号解析
node chrome-extension/e2e/extension-e2e.test.js   # Playwright 端到端（需 GUI）
```

E2E 会加载真实扩展、用 mock 的 B 站播放页与 mock 后端跑完整流程（解析渲染、
跳句、文字层/遮挡层开关、错误展示），不会访问真实 B 站，也不需要启动 daemon。
详见 `e2e/README.md`。

## 已知限制 / 后续

- 多 P 视频暂只转写第一 P（`b2text` 后端同样如此），支持指定分 P 的计划
  记录在 `docs/PLAN.md`「后续待办」。
- 远程后端需要 HTTPS + CORS + 在 manifest `host_permissions` 里加对应域名。

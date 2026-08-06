# b2text Chrome 扩展 — 实施计划

## 目标

看 B 站视频时：

1. 把当前视频（BV 号）提交给本地 b2text daemon 转写
2. 后端返回句子时间线（`[{start, end, speaker, text}]`）
3. 浏览器端用快捷键跳转到上一句 / 下一句（`<video>.currentTime = start`）
4. 可选在视频下方显示转写文字层
5. 可选显示半透明遮挡层（遮住字幕/画面，用于自测听写）

## 架构

```
B站播放页 (content script, ISOLATED world)
   │ 读取 URL 里的 BV 号 / 当前播放器元素
   ▼ chrome.runtime.sendMessage
background service worker
   │  POST /transcribe            （创建 bv 任务）
   │  GET  /tasks/{id}             （轮询状态）
   │  GET  /tasks/{id}/segments    （拿时间线 JSON）
   ▼ httpx / fetch
本地 b2text daemon (127.0.0.1:8765, 需加 CORS)
   └─ FunASR 转写 → segments 持久化
```

## 后端改动（b2text daemon）

现有 worker 转写完成后只写 txt，扩展需要结构化时间线，需要：

1. **CORS**：`build_app` 挂 `CORSMiddleware`，允许扩展来源
   （`chrome-extension://*` 或简单起见允许本地请求带 `Origin`）。
2. **segments 持久化**：worker `normalize_write` 时把
   `[{start, end, speaker, text}]` 写入 SQLite（新表 `job_segments`，
   `job_id + seq`），或写入 `data_dir/segments/<job_id>.json`。
3. **新接口** `GET /tasks/{task_id}/segments`：返回
   `{"segments": [...], "duration": 秒}`。任务未完成返回 409/202。
4. **多 P 处理**（后续）：`transcribe` 请求体可带 `cid`，扩展只转写
   当前播放的分 P，时间线与当前 P 对齐。

## 扩展模块

| 文件 | 职责 |
| --- | --- |
| `manifest.json` | MV3：权限（storage/commands）、host_permissions、content script、快捷键 |
| `background.js` | 收到 `TRANSCRIBE_BVID` → 调 daemon；轮询任务；把时间线发给 content script；`onCommand` 转发快捷键 |
| `content.js` | 解析 bvid；找 `<video>`；实现跳句（二分查找 start/end）；渲染文字层/遮挡层；状态同步 |
| `content.css` | 文字层与遮挡层的定位/样式 |
| `popup/` | 显示当前页状态、手动提交/重试、跳转设置页 |
| `options/` | 后端 URL、默认显示开关、字号/颜色/遮挡透明度 |

## 快捷键（chrome.commands）

| 命令 | 默认键 | 行为 |
| --- | --- | --- |
| `next-sentence` | Ctrl/^+Shift+→ | 跳到当前句的下一句 |
| `prev-sentence` | Ctrl/^+Shift+← | 跳到上一句的句首（句子间隙则回到最后一句） |
| `toggle-transcript` | Ctrl/^+Shift+T | 显示/隐藏文字层 |
| `toggle-overlay` | Ctrl/^+Shift+O | 显示/隐藏遮挡层 |

macOS 用 Control（^）+ Shift；不使用 Alt/Option（会输入特殊字符）。

循环播放选中句的快捷键 `Ctrl/^+Shift+L` 由 content script 内按键监听实现
（Chrome 限制单个扩展最多 4 个 `chrome.commands` 默认快捷键，选句循环
不占用该配额）。

跳句逻辑：`segments` 按 `start` 排序，二分查找当前 `currentTime` 的位置；
下一句 = 当前句的下一句；上一句 = 上一句的句首（在句子里按一次即跳到
上一句；在句子间隙则回到最近一句的句首）。

## 实施顺序

1. **脚手架**（本次）：目录、manifest、模块占位、计划文档
2. **后端 API**：CORS + `job_segments` 持久化 + `GET /tasks/{id}/segments`
   （含单测：submit→done→segments 全链路 mock）
3. **background**：任务提交/轮询/时间线缓存/命令转发（单测可 mock daemon）
4. **content script**：bvid 检测、跳句、文字层、遮挡层
5. **popup/options**：配置与状态 UI
6. **验证**：加载扩展 → 打开 B 站视频 → 提交 → 跳句/开关层；补 README 截图

## 待确认问题

已确认（2026-08-01）：

1. **后端地址**：设置页可配置，默认 `http://127.0.0.1:8765`
2. **多 P**：本期不做，记住，后续再处理
3. **文字层**：悬浮显示，可拖拽，字号可调（面板 A−/A+ 或设置页）
4. **遮挡层**：颜色在设置页选取（含预设色板），默认只遮挡视频底部字幕区，
   可拖拽移动、右下角拖拽缩放（底边锚定）

## 状态

- [x] 后端：CORS + `job_segments` 持久化 + `GET /tasks/{id}/segments`
- [x] background：提交 / 轮询 / 时间线缓存 / 命令转发
- [x] content script：bvid 检测、跳句、文字层（拖拽/字号）、遮挡层（拖拽/缩放/颜色）
- [x] 选句循环播放：勾选一句/多句 + 「循环」开关，句尾自动跳下一选中句，
  最后一句结束回到第一句（含单测）
- [x] popup / options UI
- [x] 测试：后端单测 + `node --test` seek 逻辑

## 后续待办

- **多 P 视频支持**：`/transcribe` 请求体增加 `cid`，worker 转写指定分 P，
  时间线与当前 P 对齐（已确认暂缓，下次处理）
- b23.tv 短链在跳转后即命中 content script，无需额外处理；如要覆盖
  bangumi 播放页需扩展 manifest matches
- 遮挡层/文字层位置持久化（当前每次打开重新拖）

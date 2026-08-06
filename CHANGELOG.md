# Changelog

本文件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与
[语义化版本](https://semver.org/lang/zh-CN/)。新增条目写入 `[Unreleased]`，
发版时把 `[Unreleased]` 改名成版本号并更新 `pyproject.toml` 中的 version。

## [Unreleased]

### 新增

- 新增 Playwright 端到端测试（`chrome-extension/e2e/`）：加载真实扩展，
  mock B 站播放页与后端，覆盖时间线渲染、点击/快捷键跳句、文字层与遮挡层
  开关、失败场景的错误完整展示。
- Chrome 扩展（`chrome-extension/`，MV3）：看 B 站视频时手动点击「开始解析」
  把当前 BV 号提交给本地 daemon 转写，快捷键跳上一句/下一句（Ctrl/^+Shift+←/→），
  可显示转写文字层（悬浮、可拖拽、可调字号），可显示遮挡层（底部字幕条、
  可拖拽、缩放、选色、调透明度）。
- 后端新增时间线接口 `GET /tasks/{id}/segments`；转写结果结构化写入 SQLite
  `job_segments` 表；daemon 挂 CORS 供扩展调用。
- `POST /transcribe` 的 `output_dir` 改为可选，默认 `data_dir/extension`。
- `b2text run` 恢复本地 mp4/wav/m4s 输入，并支持 `--spk-num`、`--no-overwrite`、
  `--keep-audio`、`--batch`（ugc_season 合集展开）。
- `bilibili_to_text.py` 恢复旧调用方式（自动补 `run` 子命令）。
- UP 主任务支持 `--skip-existing` 跳过已转写视频；`--limit > 50` 自动翻页
  （wbi 签名 + 完整 UA）。
- B 站 -799 限速时按 60/300/600s 退避，并联动共享限速器冷却。
- 仓库新增开发约定：`AGENTS.md`（提交规范）与 `.githooks` 本地校验。

### 变更

- 遮挡层改为只遮挡视频底部字幕区（默认高度约 1/4）：整条都可按住拖动
  （原先只有顶部小标签可拖），右下角拖拽缩放且底边保持锚定。
- 打开视频页不再自动提交转写：改为手动点击面板/弹窗的「开始解析」触发
  （解析过之后显示为「重新解析」）。

### 修复

- 上一句跳转改为一步到位：在句子里按一次直接跳到上一句句首（此前会先回
  当前句句首、再按一次才到上一句）；句子间隙则回到最近一句的句首。
- 扩展不再复用失败的缓存任务：任务 failed/cancelled 后，重新打开页面或点击
  重新解析会自动提交新任务（此前会一直展示旧的失败结果）。
- 音频下载改为使用 playurl 返回的全部候选地址（baseUrl + backupUrl，去重），
  逐个回退重试，避免单个 mcdn 节点 503 导致整个任务失败；失败信息会注明
  已尝试的 CDN 地址数量。
- 修复扩展提取 BV 号时整体转大写导致 -404（B 站 BV 号 id 部分区分大小写，
  全大写会被 view 接口判定为"啥都木有"）：改为只规范化前缀，id 保留原样；
  解析逻辑抽为 `lib/bvid.js` 并加 node 单测。
- B 站 API 业务错误不再被吞掉：`get_video_info` / `get_audio_url` 返回
  `code != 0` 时抛出 `BiliAPIError`，任务失败信息包含真实 code/message
  （如 -352 风控），`b2text run` 直跑也友好提示而非裸 traceback。
- 扩展面板失败时显示完整错误信息并可一键复制（原先只显示被截断的
  `RuntimeError`）。
- 快捷键从 Alt 改为 Ctrl/^ + Shift（macOS 用 Control），避免 Alt/Option 在
  macOS 上输入特殊字符且与系统文字导航冲突。
- 修复转写层/遮挡层关闭后无可见入口的问题：新增悬浮入口按钮，对应层
  隐藏时出现，点击即可重新打开（快捷键仍可用）。
- 修复遮挡层/文字层拖拽：指针捕获与监听元素不一致，导致松开鼠标后仍跟随
  鼠标漂移、无法固定位置；坐标统一换算为视口/宿主相对值，可精确拖拽与缩放。
- wbi 签名 keys 的并发刷新锁（原实现为死代码）。
- worker 日志 device 字段不再硬编码 mps，且对 mock 对象不再导致 JSON 序列化崩溃。
- `queue.list/count` 对空 `statuses` 不再生成非法 SQL。
- `python bilibili_to_text.py BV... -o ...` 不再报 argparse 错误。

## [0.2.0] - 2026-07

Daemon 模式（HTTP daemon + CLI）与批量任务能力。

### 新增

- `b2text serve start|stop|status|logs`：FastAPI daemon，FunASR 模型常驻内存。
- `b2text transcribe|status|list|cancel`：任务提交与查询；`b2text clean`
  按状态/时间清理历史任务。
- SQLite 任务队列（`jobs` + `job_logs` 表），daemon 重启自动恢复孤儿任务。
- UP 主批量展开（`upmaster`），共享限速器（1 req/s）避免 B 站 -799。
- `b2text list` 显示每任务当前 pipeline 步骤进度。
- 结构化 JSON Lines 日志（`JobLog`），同步镜像到 SQLite。

### 修复

- daemon.pid 改存到 data_dir（原误存 config_dir）。
- MPS 不可用时 transcriber 自动回退 CPU。
- worker 不再阻塞 uvicorn 事件循环。

## [0.1.0] - 2026-07

首个可用版本：单文件 CLI 转写。

### 新增

- `bilibili_to_text.py`：BV 号/URL/本地文件输入，FunASR
  Paraformer + CAM++ + FSMN-VAD 一体化转写，输出
  `[HH:MM:SS] Speaker_N: 文字` 格式。
- 长音频自动切片（>10 分钟），避免 FunASR 段错误。
- 毫秒→秒时间戳转换、说话人重编号、无标点空格清理。

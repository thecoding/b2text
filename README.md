# b2text

把 B 站视频中的多人对话转写为带说话人标签的纯文本，完全本地运行。

## 特性

- 🎙️ ASR（Paraformer-large）+ 说话人日志（CAM++）+ VAD 一体化
- 💻 Apple Silicon Metal 加速（M1/M2/M3/M4）
- 📦 完全本地，无云服务依赖（FunASR 后端）
- 🔌 **omlx 后端（待实现）**：计划调用本地 omlx 服务的 `/v1/audio/transcriptions`。当前代码尚未包含；需要 omlx ≥ 0.5.0 才暴露该路由
- 🎯 输出 `[HH:MM:SS] Speaker_N: 文字` 格式（FunASR 后端）
- 📚 支持 BV 号、URL、本地 mp4 / wav 输入
- 🗂️ 自动识别合集（ugc_season），支持批量转写

## 安装

```bash
brew install ffmpeg

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**模型下载**：首次运行会自动从 ModelScope 下载 Paraformer-large + CAM++ + FSMN-VAD 模型（约 1.3 GB），缓存到 `~/.cache/modelscope/`。再次运行直接用本地缓存。

## 快速开始

```bash
# 从 BV 号下载并转写
python bilibili_to_text.py BV1xxxxxxxxxx -o output.txt

# 处理已有的本地 mp4
python bilibili_to_text.py ./downloads/xxx/001.mp4 -o output.txt

# 批量合集（自动展开 ugc_season，每集一个 txt）
python bilibili_to_text.py BV1xxxxxxxxxx --batch -o ./texts/
```

## 用法

```
python bilibili_to_text.py <BV号|URL|mp4|wav路径> -o <输出路径> [选项]
```

| 参数 | 说明 |
| --- | --- |
| `input` | BV 号、URL、本地 mp4 / wav / m4s 路径（自动识别） |
| `-o` / `--output` | 单文件模式：输出 txt 路径；批量模式：输出目录 |
| `--batch` | 批量模式：处理 `ugc_season` 合集所有视频 |
| `--device mps\|cpu` | 推理设备。默认 `mps`（Apple Silicon 加速），Intel Mac 用 `cpu` |
| `--spk-num N` | 已知说话人数量。指定后可提升多说话人日志的准确度 |
| `--no-overwrite` | 跳过已存在的输出文件（默认覆盖） |
| `--keep-audio` | 在输出目录保留 wav 文件，便于复现或调试 |

## 输出格式

每行一段转写结果：

```
[HH:MM:SS] Speaker_N: 文字
```

- `HH:MM:SS` 从视频起始计算
- `Speaker_N` 按该说话人在该视频中首次出现顺序编号
- 同一说话人后续出现的段继续使用同一个 `Speaker_N` 标签

示例：

```
[00:00:15] Speaker_1: 大家好欢迎来到本期节目
[00:00:23] Speaker_2: 今天我们来聊一聊最近比较火的一个话题
[00:00:35] Speaker_1: 对这个话题我有几个看法
```

## 路线图

**omlx 后端**：当前 README 早期版本描述了用本地 omlx 服务（`http://localhost:8000/v1/audio/transcriptions`）作为可选后端的方案。**该功能尚未实现**。omlx 的音频路由在 0.5.0 才加入，且部分模型（如 `Fun-ASR-Nano`）在本机 omlx 0.2.20.dev2 上被发现但未加载——`POST /v1/chat/completions` 调用会返回 `Model type funasr not supported`，且 `/v1/audio/transcriptions` 路由未注册。

要使用 omlx 后端需要：

1. 升级 omlx 到 ≥ 0.5.0
2. 确认服务里至少加载了一个 STT 模型（`GET /v1/models` 应包含 Whisper / Qwen3-ASR / Fun-ASR 类）
3. 在 b2text 实现 `b2text/omlx_backend.py` 并接入 CLI

如果你已经在用 ≥ 0.5.0 并能跑出 `POST /v1/audio/transcriptions` 的响应，请提个 issue 提醒一下实现这部分。

## 工作原理

一次前向完成 ASR + VAD + 说话人日志：

```
B 站 / 本地文件
        │
        ▼
  ffmpeg 抽音轨 ──▶ 16kHz mono WAV
                      │
                      ▼
              FunASR AutoModel
        ┌──────────────┼──────────────┐
        │              │              │
      Paraformer    FSMN-VAD       CAM++
      (ASR 文字)    (语音段切分)   (说话人聚类)
        └──────────────┴──────────────┘
                      │
                      ▼
           sentence_info: [{start, end, sentence, spk}, ...]
                      │
                      ▼
            normalizer (去空段 + 说话人重编号)
                      │
                      ▼
              format_segments ──▶ output.txt
```

## 性能

M1 Pro 32 GB 实测：~17s 音频 → ~1s 转写（rtf ≈ 0.05）。CPU 上 rtf ≈ 0.27，约 4 倍实时。

首次运行会下载模型（约 1.3 GB）。后续每次运行启动延迟约 3–5 秒（模型加载），转写本体的速度由上表给出。

## 测试

Daemon 模式依赖 `fastapi`/`uvicorn`/`httpx`（已在 requirements.txt 中）。

```bash
pytest -v                          # 全部测试（集成测试需 FunASR + 模型）
pytest -m "not integration" -v     # 仅单元测试（无需模型）
```

## 故障排查

**`未找到 ffmpeg`**
```bash
brew install ffmpeg   # macOS
sudo apt install ffmpeg   # Linux
```

**`获取视频信息失败` / `code: -101` / `code: -352`**

仓库内置的 `COOKIE` 是占位符（`YOUR_BILIBILI_COOKIE_HERE`），需要替换成你自己的：

1. 浏览器登录 bilibili.com，F12 → Network → 任意请求 → 复制 `Cookie` 头
2. 重点保留 `SESSDATA=...; bili_jct=...` 两段
3. 同时改两个文件（cookie 在两处都需要）：
   - `b2text/bili_api.py`（用于调 B 站 API 取元数据）
   - `b2text/audio.py`（用于 curl 下载 m4s 音频流）
4. 把两处的 `COOKIE = "YOUR_BILIBILI_COOKIE_HERE"` 替换成真实 cookie

更彻底的方案是实现 cookie 自动刷新（见 `bilibili_batch_downloader.py`）。

**`punc_model is missing, falling back to vad_segment mode`**

这不是错误，只是说没有加载标点模型（项目默认不依赖）。后果：输出文本没有标点，且 FunASR 会在中文字符之间插空格——`b2text/formatter.py` 已经自动折叠这些空格，但想要带标点的输出可以加载 punc 模型（暂未支持）。

**`NotImplementedError: … with MPS backend`**

某些 FunASR 子模块（早期版本）不支持 MPS。回退：
```bash
python bilibili_to_text.py BV1xxxxxxxxxx -o output.txt --device cpu
```

**模型重新下载**

```bash
rm -rf ~/.cache/modelscope/   # 强制下次运行时重新下载
```

## 限制

- **没有标点**：默认不加载 punc 模型，输出文本无标点且字间可能有空格（已自动清理）。要带标点请修改 `transcriber.py` 添加 `punc_model='ct-punc'`。
- **单 P 视频优先**：`--batch` 模式按合集中每集的 `cid` 拉音轨；但单 P 视频天然只有一个 `cid`，所以也支持。
- **依赖网络**：下载音频流需要能访问 B 站（直连 / 代理 / Cookie 都能影响）。
- **长视频内存**：长视频（>1 小时）峰值内存较高；实测 3 小时单集在 M1 Pro 32 GB 上可正常完成。
- **多说话人聚类**：CAM++ 默认自动检测说话人数。已知数量时传 `--spk-num N` 可帮助聚类（具体效果依音频而定）。

## 与现有下载器的关系

本项目与 `bilibili_batch_downloader.py` 完全独立。下载器只下载视频；本工具只转文本。如需完整流程：先用下载器，再用本工具处理 mp4。

## Daemon 模式（v2）

把模型常驻内存，多次提交不用重新加载 FunASR。任务经由本地 HTTP daemon（`127.0.0.1:8765`）排队，SQLite 持久化，重启自动恢复未完成任务。

### 准备 cookie

```bash
mkdir -p ~/.config/b2text
echo "SESSDATA=xxx; bili_jct=xxx" > ~/.config/b2text/cookie
chmod 600 ~/.config/b2text/cookie
```

也可用环境变量一次性覆盖：`export B2TEXT_COOKIE="..."`。文件优先于环境变量。

### 启动

```bash
b2text serve start
# 等几十秒模型加载完成
b2text serve status   # 看 model_loaded=true 后再提交任务
```

### 提交任务

```bash
# 单个 BV
b2text transcribe BV1xxxxxxxxx -o /Users/me/sourceRead/

# 整个 UP 主（默认最新 50 条，可用 --limit 限制）
b2text transcribe --type up 12345678 -o /Users/me/sourceRead/ --limit 30

# 查状态
b2text status <task_id>
b2text list
b2text list --status running

# 取消排队中的任务
b2text cancel <task_id>
```

### 看日志

```bash
b2text serve logs                       # tail daemon.log
cat ~/.local/share/b2text/jobs.log      # 结构化 JSON Lines（每任务每步一行）
```

### 关闭

```bash
b2text serve stop
```

### 调试逃生口（不走 daemon）

```bash
b2text run BV1xxxxxxxxx -o /tmp/x.txt
```

直接本地同步跑，模型每次重新加载。

## 设计文档

- 设计 spec: `docs/superpowers/specs/2026-07-10-b2text-design.md`
- 实施计划: `docs/superpowers/plans/2026-07-10-b2text.md`
# b2text

把 B 站视频中的多人对话转写为带说话人标签的纯文本，完全本地运行。

## 特性

- 🎙️ ASR（Paraformer-large）+ 说话人日志（CAM++）+ VAD 一体化
- 💻 Apple Silicon Metal 加速（M1/M2/M3/M4）
- 📦 完全本地，无云服务依赖（FunASR 后端）
- 🔌 可选 omlx 后端：把音频发到本地 omlx 服务（`http://localhost:8000`），跳过本地模型加载
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
# 从 BV 号下载并转写（默认 funasr 后端）
python bilibili_to_text.py BV1xxxxxxxxxx -o output.txt

# 处理已有的本地 mp4
python bilibili_to_text.py ./downloads/xxx/001.mp4 -o output.txt

# 批量合集（自动展开 ugc_season，每集一个 txt）
python bilibili_to_text.py BV1xxxxxxxxxx --batch -o ./texts/

# 用本地 omlx 服务转写（要求 omlx 在 localhost:8000 跑着）
python bilibili_to_text.py BV1xxxxxxxxxx -o output.txt --backend omlx
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
| `--backend funasr\|omlx` | 转写后端。默认 `funasr`（本地一体化 ASR+说话人日志），`omlx` 调用本地 omlx 服务（纯文本，无说话人） |
| `--device mps\|cpu` | FunASR 后端的推理设备。默认 `mps`（Apple Silicon 加速），Intel Mac 用 `cpu`。`--backend omlx` 时忽略 |
| `--spk-num N` | 已知说话人数量（FunASR 后端有效，`omlx` 后端忽略） |
| `--omlx-model NAME` | omlx 后端使用的模型 ID（如 `whisper-large-v3-turbo`、`qwen3-asr`），默认 `whisper-large-v3-turbo` |
| `--no-overwrite` | 跳过已存在的输出文件（默认覆盖） |
| `--keep-audio` | 在输出目录保留 wav 文件，便于复现或调试 |

## 输出格式

**funasr 后端**（默认）—— 每行一段带说话人标签：

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

**omlx 后端** —— 纯文本，逐行（按换行/句号切分），无时间戳、无说话人：

```
大家好欢迎来到本期节目
今天我们来聊一聊最近比较火的一个话题
对这个话题我有几个看法
```

## 后端选择

| 维度 | funasr（默认） | omlx |
| --- | --- | --- |
| 说话人区分 | ✅ Speaker_1 / Speaker_2 / … | ❌ 不区分 |
| 时间戳 | ✅ 每段带 `[HH:MM:SS]` | ❌ 无 |
| 首次启动 | 需下载 ~1.3 GB 模型 | 需 omlx 服务已启动 |
| 推理速度 | rtf ≈ 0.05（M1 Pro，17 s 音频 ~1 s 转完） | 取决于 omlx 服务的模型和硬件 |
| 离线运行 | ✅ 模型下载后可离线 | ❌ 必须能连到 omlx 服务 |
| 后端依赖 | `funasr`, `modelscope`, `torch` | `mlx-audio`（由 omlx 自带） |

**何时用 omlx**：已有 omlx 服务在跑、不需要说话人区分、希望跳过模型下载、或 omlx 端有更准确的 Whisper / Qwen3-ASR 模型可用。

**何时用 funasr**：需要说话人区分、需要时间戳、希望完全离线、或懒得另外启动 omlx。

## 工作原理

**funasr 后端**（默认）—— 一次前向完成 ASR + VAD + 说话人日志：

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

**omlx 后端** —— 本地推理交给 omlx 服务，b2text 只负责抽音轨和切分行：

```
B 站 / 本地文件
        │
        ▼
  ffmpeg 抽音轨 ──▶ 16kHz mono WAV
                      │
                      ▼
            POST http://localhost:8000/v1/audio/transcriptions
                      │
                      ▼
                   {"text": "..."}
                      │
                      ▼
              按 \n 切行 ──▶ output.txt
```

## 性能

M1 Pro 32 GB 实测：~17s 音频 → ~1s 转写（rtf ≈ 0.05）。CPU 上 rtf ≈ 0.27，约 4 倍实时。

首次运行会下载模型（约 1.3 GB）。后续每次运行启动延迟约 3–5 秒（模型加载），转写本体的速度由上表给出。

## 测试

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

B 站的 cookie 会过期。打开 `b2text/bili_api.py`，把 `COOKIE` 替换成新的 SESSDATA：

1. 浏览器登录 bilibili.com，F12 → Network → 任意请求 → 复制 `Cookie` 头
2. 重点保留 `SESSDATA=...; bili_jct=...` 两段
3. 粘贴到 `COOKIE = "..."`，保持 `buvid4` 在前

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

**`omlx 后端：Connection refused` / 无法连接**

`--backend omlx` 默认指向 `http://localhost:8000/v1`。检查：

```bash
# 1. omlx 是否在跑
curl http://localhost:8000/v1/models

# 2. 服务是否提供音频模型（如 whisper-large-v3-turbo、qwen3-asr）
curl http://localhost:8000/v1/models | grep -i 'whisper\|qwen3-asr\|parakeet'

# 3. 用环境变量改地址（如果 omlx 不在 8000）
B2TEXT_OMLX_URL=http://localhost:9000/v1 python bilibili_to_text.py BV1xxx -o out.txt --backend omlx

# 4. 切换模型（如果默认的 whisper-large-v3-turbo 不可用）
python bilibili_to_text.py BV1xxx -o out.txt --backend omlx --omlx-model qwen3-asr
```

**`omlx 后端：HTTP 400 / 404`**

服务端没加载对应模型。`GET /v1/models` 列出可用模型名，把列表里的名字传 `--omlx-model`。

**`omlx 后端：HTTP 500`**

omlx 服务内部错误。检查 omlx 日志、模型加载情况，或换一个模型试试。

## 限制

- **没有标点**：默认不加载 punc 模型，输出文本无标点且字间可能有空格（已自动清理）。要带标点请修改 `transcriber.py` 添加 `punc_model='ct-punc'`。
- **单 P 视频优先**：`--batch` 模式按合集中每集的 `cid` 拉音轨；但单 P 视频天然只有一个 `cid`，所以也支持。
- **依赖网络**：下载音频流需要能访问 B 站（直连 / 代理 / Cookie 都能影响）。
- **长视频内存**：长视频（>1 小时）峰值内存较高；实测 3 小时单集在 M1 Pro 32 GB 上可正常完成。
- **多说话人聚类**：CAM++ 默认自动检测说话人数。已知数量时传 `--spk-num N` 可帮助聚类（具体效果依音频而定）。仅 funasr 后端有效。
- **omlx 后端无说话人/时间戳**：omlx 的 `/v1/audio/transcriptions` 走 OpenAI 兼容接口，返回纯文本。需要说话人区分或时间戳时必须用 funasr 后端。

## 与现有下载器的关系

本项目与 `bilibili_batch_downloader.py` 完全独立。下载器只下载视频；本工具只转文本。如需完整流程：先用下载器，再用本工具处理 mp4。

## 设计文档

- 设计 spec: `docs/superpowers/specs/2026-07-10-b2text-design.md`
- 实施计划: `docs/superpowers/plans/2026-07-10-b2text.md`
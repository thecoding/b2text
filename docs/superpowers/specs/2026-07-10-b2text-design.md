# B站视频对话转文本（带说话人区分）设计文档

**日期**: 2026-07-10
**状态**: 设计中
**作者**: Claude（与用户协作）

## 背景

当前 `/Users/mirko/source/b2text/` 目录下已有 `bilibili_batch_downloader.py`，能批量下载B站视频（支持 ugc_season 合集），下载到本地 mp4。用户希望**新增一个独立脚本**，把视频中的多人对话转写为纯文本，并区分不同说话人。

## 目标

- 输入：B站 BV号/URL，或本地 mp4 路径
- 输出：纯文本文件，格式为 `[时间戳] Speaker_N: 文字`
- 完全本地运行，无需云服务、无需付费 API
- 在 M1 Pro 32GB 统一内存 Mac 上流畅运行
- 不修改现有下载器代码

## 非目标（YAGNI）

- ❌ 网页 UI
- ❌ 实时转写
- ❌ 说话人自动命名（只输出 Speaker_N）
- ❌ 翻译
- ❌ 视频摘要
- ❌ 历史记录持久化
- ❌ 重构现有下载器

## 技术选型

| 组件 | 选型 | 理由 |
|---|---|---|
| ASR | FunASR Paraformer-large | 中文 SOTA，比 Whisper large-v3 在中文上更准 |
| 说话人日志 | FunASR CAM++ | 达摩院原生支持，与 ASR 同框架一体化 |
| 静音检测 | FunASR FSMN-VAD | 与 ASR 配套 |
| 音频下载 | curl（参考现有下载器风格） | 保持风格一致、零依赖 |
| 音频转换 | ffmpeg（系统命令） | 行业标准 |
| 模型源 | ModelScope | 国内 CDN，无需翻墙 |
| Python 包管理 | pip + requirements.txt | 简单直接 |

## 架构

### 模块边界

```
bilibili_to_text.py (CLI 入口)
  └── b2text/
      ├── audio.py        # BV→音频流 / mp4→WAV
      ├── transcriber.py  # FunASR ASR 封装
      ├── diarizer.py     # FunASR CAM++ 说话人日志
      ├── aligner.py      # ASR + Diarization + VAD 三方对齐
      ├── formatter.py    # 输出文本格式化
      └── utils.py        # BV号提取、文件工具
```

每个模块独立可替换、可单测。

### 数据流

```
输入(BV/URL/mp4)
  │
  ▼
[audio.py] ──→ 16kHz mono WAV
  │
  ▼
[transcriber.py] ──→ 段列表[(start, end, text)]
  │
  ▼
[diarizer.py] ──→ 段列表[(start, end, speaker_id)]
  │
  ▼
[aligner.py] ──→ 段列表[(start, end, speaker_id, text)]
  │              (按时间戳 IoU 最大匹配)
  ▼
[formatter.py] ──→ 纯文本
  │
  ▼
输出文件
```

### FunASR 内部 pipeline

FunASR 支持一体化 AutoModel：

```python
model = AutoModel(
    model="paraformer-zh",
    vad_model="fsmn-vad",
    spk_model="cam++",
    spk_num_auto=True,  # 自动检测说话人数量
)
result = model.generate(input=wav_path)
```

返回结果中每个 segment 自带 `spk` 字段，无需手动对齐。`aligner.py` 主要负责把 FunASR 输出规整为统一数据结构。

## 项目结构

```
/Users/mirko/source/b2text/
├── bilibili_batch_downloader.py   # 现有，不动
├── bilibili_to_text.py            # 新脚本入口
├── b2text/
│   ├── __init__.py
│   ├── audio.py
│   ├── transcriber.py
│   ├── diarizer.py
│   ├── aligner.py
│   ├── formatter.py
│   └── utils.py
├── tests/
│   ├── test_aligner.py
│   ├── test_formatter.py
│   ├── test_utils.py
│   └── fixtures/
│       └── sample_5s.wav
├── docs/
│   └── superpowers/
│       └── specs/
│           └── 2026-07-10-b2text-design.md
├── requirements.txt
└── README.md
```

## CLI 设计

```bash
# 用法1：从BV号下载并转写
python bilibili_to_text.py BV1xxxxxxxxxx -o output.txt

# 用法2：处理本地 mp4
python bilibili_to_text.py ./downloads/xxx/001.mp4 -o output.txt

# 用法3：批量合集
python bilibili_to_text.py BV1xxxxxxxxxx --batch -o ./texts/

# 用法4：指定说话人数量（已知）
python bilibili_to_text.py BV1xxxxxxxxxx --spk-num 2 -o output.txt
```

### 参数

| 参数 | 说明 | 默认 |
|---|---|---|
| positional | BV号/URL 或 mp4 路径 | 必填 |
| `-o / --output` | 输出文件/目录 | 必填 |
| `--batch` | 合集批量模式 | False |
| `--spk-num` | 已知说话人数量 | None（自动检测） |
| `--device` | mps / cpu | mps |
| `--no-overwrite` | 不覆盖已存在输出 | False |
| `--keep-audio` | 保留中间音频文件 | False |

## 输出格式

```
[00:00:15] Speaker_1: 大家好欢迎来到本期节目
[00:00:23] Speaker_2: 今天我们来聊一聊最近比较火的一个话题
[00:00:35] Speaker_1: 对这个话题我有几个看法
```

- Speaker_N 按首次出现顺序编号（不是按说话时长）
- 时间戳 HH:MM:SS 精度足够定位大段对话
- 每行一段；空行分隔明显停顿

## 错误处理

| 场景 | 行为 | 退出码 |
|---|---|---|
| BV号无效 | 打印错误退出 | 1 |
| 网络下载失败 | 重试2次×5s，仍失败退出 | 2 |
| ffmpeg 未安装 | 启动前检测，提示安装命令 | 3 |
| 模型下载失败 | 提示检查网络 | 4 |
| 静音音频 | 输出空文件+警告 | 0 |
| 0 说话人 | 退化为 Speaker_1 | 0 |
| MPS 不可用 | 自动 fallback CPU+警告 | 0 |
| 输出已存在 | 默认覆盖，`--no-overwrite` 跳过 | 0 |

## 性能预期（M1 Pro 32GB 实测参考）

| 视频时长 | 处理耗时 |
|---|---|
| 1 分钟 | 1-2 分钟 |
| 10 分钟 | 3-5 分钟 |
| 1 小时 | 15-20 分钟 |

瓶颈在 Paraformer-large ASR；CAM++ 说话人日志较快。

## 测试策略

| 层级 | 内容 | 离线可运行 |
|---|---|---|
| 单元测试 | 对齐、格式化、BV号提取 | ✓ |
| 集成测试 | sample_5s.wav 完整 pipeline | ✓（首次需模型） |
| 验收测试 | TED Talks 5分钟中字样本 | ✗（需下载） |

CI 不强制要求 FunASR 模型下载；模型缺失时集成测试 skip。

## 依赖

### requirements.txt

```
funasr>=1.1.0
modelscope>=1.10.0
torch>=2.0.0
numpy
```

### 系统依赖

- ffmpeg（`brew install ffmpeg`）
- Python 3.10+

### 模型（首次运行自动下载，缓存到 ~/.cache/modelscope/）

- paraformer-zh (~1GB)
- cam++ (~270MB)
- fsmn-vad (~50MB)

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| FunASR 在 MPS 上的兼容性 | 启动时检测，失败 fallback CPU |
| 模型下载失败/慢 | 给出明确错误和重试提示 |
| 长视频内存溢出 | 2小时以上自动按 30 分钟切片 |
| 说话人重叠 / 多人抢话 | CAM++ 处理重叠有限，文档说明局限 |
| 背景音乐干扰 | 文档说明预期准确度下降 |
| B站音频流鉴权变化 | 参考现有下载器的 cookie 处理 |

## 验收标准

1. ✅ 给定一个多人对话的 BV号，30 分钟内生成纯文本文件
2. ✅ 文本格式正确：每行 `[时间戳] Speaker_N: 文字`
3. ✅ 不同说话人的句子归属基本正确（人工抽检 10 段，正确率 ≥ 80%）
4. ✅ 中文识别准确率 ≥ 90%（人工抽检 100 字）
5. ✅ M1 Pro 32GB 上不卡顿，不需重启
6. ✅ 单条命令完成，不需要中途手动操作
7. ✅ 失败时给出明确错误信息和恢复建议

## 后续（不在本次实现）

- 说话人自动命名（结合视频简介/字幕上下文推测）
- Web UI
- 翻译成其他语言
- 视频摘要生成

## 参考

- FunASR: https://github.com/modelscope/FunASR
- Paraformer: https://www.modelscope.cn/models/damo/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-pytorch
- CAM++: https://www.modelscope.cn/models/damo/speech_campplus_sv_zh-cn_16k
- 现有下载器: `/Users/mirko/source/b2text/bilibili_batch_downloader.py`
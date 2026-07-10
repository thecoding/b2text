# b2text

把B站视频中的多人对话转写为带说话人标签的纯文本，完全本地运行。

## 特性

- 🎙️ ASR（Paraformer-large）+ 说话人日志（CAM++）+ VAD 一体化
- 💻 Apple Silicon Metal 加速（M1/M2/M3/M4）
- 📦 完全本地，无云服务依赖
- 🎯 输出 `[HH:MM:SS] Speaker_N: 文字` 格式
- 📚 支持 BV 号、URL、本地 mp4 输入

## 安装

```bash
brew install ffmpeg
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

首次运行会自动从 ModelScope 下载模型（约 1.3GB，缓存到 `~/.cache/modelscope/`）。

## 用法

```bash
# 从 BV 号下载并转写
python bilibili_to_text.py BV1xxxxxxxxxx -o output.txt

# 处理本地 mp4
python bilibili_to_text.py ./downloads/xxx/001.mp4 -o output.txt

# 批量合集（每个视频独立输出）
python bilibili_to_text.py BV1xxxxxxxxxx --batch -o ./texts/

# 强制使用 CPU
python bilibili_to_text.py BV1xxxxxxxxxx -o output.txt --device cpu
```

## 输出示例

```
[00:00:15] Speaker_1: 大家好欢迎来到本期节目
[00:00:23] Speaker_2: 今天我们来聊一聊最近比较火的一个话题
[00:00:35] Speaker_1: 对这个话题我有几个看法
```

## 测试

```bash
pytest -v                # 全部测试（集成测试需模型）
pytest -m "not integration" -v   # 仅单元测试
```

## 与现有下载器的关系

本项目与 `bilibili_batch_downloader.py` 完全独立。下载器只下载视频；本工具只转文本。如需完整流程：先用下载器，再用本工具处理 mp4。

## 设计文档

- 设计 spec: `docs/superpowers/specs/2026-07-10-b2text-design.md`
- 实施计划: `docs/superpowers/plans/2026-07-10-b2text.md`

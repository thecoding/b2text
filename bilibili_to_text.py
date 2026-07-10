#!/usr/bin/env python3
"""B站视频对话转文本 CLI 入口。

用法:
  python bilibili_to_text.py <BV号|URL|mp4路径> -o <输出文件>
  python bilibili_to_text.py <BV号> --batch -o <输出目录>
"""

import argparse
import re
import sys
import tempfile
from pathlib import Path

from b2text.audio import check_ffmpeg, download_audio_stream, ensure_wav
from b2text.bili_api import (
    extract_series_videos,
    get_audio_url,
    get_video_info,
)
from b2text.formatter import format_segments
from b2text.normalizer import normalize_funasr_output
from b2text.transcriber import FunASRTranscriber
from b2text.utils import extract_bvid


def is_local_path(s: str) -> bool:
    """判断是否是本地文件路径（不是 BV 号或 URL）。"""
    return Path(s).exists() or s.endswith((".mp4", ".wav", ".m4s"))


def safe_filename(title: str) -> str:
    """清理文件名非法字符。"""
    return re.sub(r'[<>:"/\\|?*]', "_", title)[:60]


def process_single_video(
    input_arg: str,
    output: Path,
    transcriber: FunASRTranscriber,
    keep_audio: bool,
    overwrite: bool,
) -> bool:
    """处理单个视频，返回成功与否。"""
    if output.exists() and not overwrite:
        print(f"⏭️  跳过已存在: {output}")
        return False

    # 1. 准备音频
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        if is_local_path(input_arg):
            # 本地文件
            source = Path(input_arg)
            print(f"📂 使用本地文件: {source}")
            wav_path = ensure_wav(source, tmpdir)
        else:
            # BV 号 / URL
            bvid = extract_bvid(input_arg)
            if not bvid:
                print(f"❌ 无法识别输入: {input_arg}")
                return False
            print(f"🔍 查询视频信息: {bvid}")
            info = get_video_info(bvid)
            if not info:
                print(f"❌ 获取视频信息失败: {bvid}")
                return False
            print(f"📺 标题: {info['title']}")

            page = info["pages"][0]
            url = get_audio_url(info["aid"], page["cid"])
            if not url:
                print("❌ 获取音频链接失败")
                return False

            m4s_path = tmpdir / "audio.m4s"
            print("📥 下载音频流...")
            try:
                download_audio_stream(url, m4s_path)
            except RuntimeError as e:
                print(f"❌ {e}")
                return False
            wav_path = ensure_wav(m4s_path, tmpdir)

        # 2. 转写
        print("🎙️  开始转写（首次运行会下载 FunASR 模型，约 1.3GB）...")
        try:
            raw = transcriber.transcribe(wav_path)
        except Exception as e:
            print(f"❌ 转写失败: {e}")
            return False

        # 3. 规范化
        segments = normalize_funasr_output(raw)

        # 4. 输出
        output.parent.mkdir(parents=True, exist_ok=True)
        text = format_segments(segments)
        output.write_text(text, encoding="utf-8")

    print(f"✅ 已写入 {output}（{len(segments)} 段）")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="B站视频对话转文本（带说话人区分）"
    )
    parser.add_argument(
        "input",
        help="BV号 / URL / 本地mp4或wav路径",
    )
    parser.add_argument(
        "-o", "--output", required=True,
        help="输出文件路径（单文件模式）或目录（批量模式）",
    )
    parser.add_argument(
        "--batch", action="store_true",
        help="批量模式：处理 ugc_season 合集所有视频，输出到 -o 目录",
    )
    parser.add_argument(
        "--device", default="mps", choices=["mps", "cpu"],
        help="推理设备（默认 mps）",
    )
    parser.add_argument(
        "--spk-num", type=int, default=None,
        help="已知说话人数量（不指定则自动检测）",
    )
    parser.add_argument(
        "--no-overwrite", action="store_true",
        help="不覆盖已存在的输出文件",
    )
    parser.add_argument(
        "--keep-audio", action="store_true",
        help="保留中间音频文件",
    )

    args = parser.parse_args()

    # 前置检查
    if not check_ffmpeg():
        print("❌ 未找到 ffmpeg。请先安装：brew install ffmpeg")
        sys.exit(3)

    transcriber = FunASRTranscriber(
        device=args.device,
        spk_num=args.spk_num,
    )

    if args.batch:
        # 批量模式
        bvid = extract_bvid(args.input)
        if not bvid:
            print("❌ --batch 模式需要 BV 号或 URL")
            sys.exit(1)
        info = get_video_info(bvid)
        if not info:
            print("❌ 获取视频信息失败")
            sys.exit(1)
        ugc_season = info.get("ugc_season")
        if not ugc_season:
            print("❌ 该视频不是合集，无需 --batch")
            sys.exit(1)
        videos = extract_series_videos(ugc_season)
        output_dir = Path(args.output)
        output_dir.mkdir(parents=True, exist_ok=True)

        for i, video in enumerate(videos, 1):
            print(f"\n[{i}/{len(videos)}] {video['title']}")
            out_file = output_dir / f"{i:03d}_{safe_filename(video['title'])}.txt"
            process_single_video(
                video["bvid"], out_file, transcriber,
                args.keep_audio,
                overwrite=not args.no_overwrite,
            )
    else:
        output = Path(args.output)
        ok = process_single_video(
            args.input, output, transcriber,
            args.keep_audio,
            overwrite=not args.no_overwrite,
        )
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

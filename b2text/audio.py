"""音频下载、抽取与 WAV 转换。

依赖系统命令：ffmpeg；HTTP 下载使用 httpx。
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

import httpx


def check_ffmpeg() -> bool:
    """检查系统是否安装了 ffmpeg。"""
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def extract_audio_from_mp4(mp4_path: Path, wav_path: Path) -> Path:
    """用 ffmpeg 把 mp4 转 16kHz mono WAV。失败抛 RuntimeError。"""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(mp4_path),
        "-ar", "16000",
        "-ac", "1",
        "-f", "wav",
        str(wav_path),
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg 转换失败 (exit {result.returncode}): {result.stderr.decode(errors='ignore')[:200]}"
        )
    return wav_path


def _download_audio_one(url: str, output: Path, *, cookie: str) -> None:
    """下载单个 URL；HTTP/空文件失败抛 RuntimeError。"""
    headers = {
        "Cookie": cookie,
        "Referer": "https://www.bilibili.com",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    try:
        with httpx.Client(timeout=600.0, follow_redirects=True) as client:
            r = client.get(url, headers=headers)
            r.raise_for_status()
            output.write_bytes(r.content)
    except httpx.HTTPError as e:
        raise RuntimeError(f"音频下载失败: {e}")
    if not output.exists() or output.stat().st_size == 0:
        raise RuntimeError(f"音频下载失败：文件为空或不存在 {output}")


def download_audio_stream(url: str, output: Path, *, cookie: str) -> Path:
    """用 httpx 下载单个音频流（m4s）。失败抛 RuntimeError。"""
    _download_audio_one(url, output, cookie=cookie)
    return output


def download_audio_stream_candidates(
    urls: list[str],
    output: Path,
    *,
    cookie: str,
    attempts_per_url: int = 2,
    gap_seconds: float = 1.0,
) -> Path:
    """按顺序尝试多个 CDN 地址（baseUrl + backupUrl），全部失败抛 RuntimeError。

    B 站 playurl 返回的某个 CDN 节点（尤其 mcdn 的 http 直连地址）可能
    503/超时；backupUrl 通常是 https 的 upos 节点，逐个回退可显著提高成功率。
    """
    if not urls:
        raise RuntimeError("音频下载失败：没有可用的音频地址")
    last_err: Exception | None = None
    for url in urls:
        for attempt in range(attempts_per_url):
            try:
                _download_audio_one(url, output, cookie=cookie)
                return output
            except RuntimeError as e:
                last_err = e
                if attempt < attempts_per_url - 1:
                    time.sleep(gap_seconds)
    raise RuntimeError(
        f"音频下载失败（已尝试 {len(urls)} 个 CDN 地址、每个 {attempts_per_url} 次）: {last_err}"
    )


def ensure_wav(source: Path, output_dir: Path) -> Path:
    """如果 source 已经是 wav，直接返回；否则转 wav 到 output_dir。"""
    if source.suffix.lower() == ".wav":
        return source
    wav_path = output_dir / (source.stem + ".wav")
    return extract_audio_from_mp4(source, wav_path)


def get_wav_duration_seconds(wav_path: Path) -> float:
    """用 ffprobe 读 WAV 时长（秒）。失败抛 RuntimeError。"""
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            str(wav_path),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"ffprobe 失败: {result.stderr[:200]}")
    return float(result.stdout.strip())


def chunk_wav(wav_path: Path, output_dir: Path, chunk_seconds: int) -> list[tuple[Path, float]]:
    """把 WAV 切成等长片段，返回 [(path, offset_seconds), ...]。

    使用 ffmpeg segment muxer，最后一段可能较短。失败抛 RuntimeError。
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    pattern = output_dir / f"{wav_path.stem}_chunk_%04d.wav"
    cmd = [
        "ffmpeg", "-y",
        "-i", str(wav_path),
        "-f", "segment",
        "-segment_time", str(chunk_seconds),
        "-reset_timestamps", "0",
        "-ar", "16000", "-ac", "1",
        str(pattern),
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg 切片失败 (exit {result.returncode}): "
            f"{result.stderr.decode(errors='ignore')[:200]}"
        )
    chunks = sorted(output_dir.glob(f"{wav_path.stem}_chunk_*.wav"))
    return [(p, i * chunk_seconds) for i, p in enumerate(chunks)]

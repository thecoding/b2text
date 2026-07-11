"""音频下载、抽取与 WAV 转换。

依赖系统命令：ffmpeg、curl
"""
import subprocess
from pathlib import Path

COOKIE = (
    "buvid4=D21E6012-4A38-B23C-2BC5-7961BE48BEDE62503-024092215-3n5xeHPj8bn9aScYIf2pzg%3D%3D; "
    "SESSDATA=02e002c7%2C1778164764%2C4ad7b%2Ab1CjAFHRTtmUbXSwancqb8IOrEITiLH-OCPDF8YgnZZoJyUC4S2hy63a6JiY0UlRuu-lMSVnlzWVBxRUFOOUx3bmZQZEF1RnlnRGxhNlprLXZLejQwTmtvMWdjNm9mTldXd3M0ZHVCWGJVdzVmb2FuOGpkalc5WHhycnJQdDlKMFNzZ0U2TkZkN19RIIEC; "
    "bili_jct=169d89ed657d4564dd1e190a04ec1acd"
)


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


def download_audio_stream(url: str, output: Path, cookie: str = COOKIE) -> Path:
    """用 curl 下载音频流（m4s）。失败抛 RuntimeError。"""
    cmd = [
        "curl", "-L", "-C", "-",
        "-o", str(output),
        "-H", f"Cookie: {cookie}",
        "-H", "Referer: https://www.bilibili.com",
        "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "--max-time", "600",
        url,
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"音频下载失败 (exit {result.returncode})")
    if not output.exists() or output.stat().st_size == 0:
        raise RuntimeError(f"音频下载失败：文件为空或不存在 {output}")
    return output


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
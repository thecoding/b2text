import subprocess
from pathlib import Path
import pytest
from b2text.audio import (
    check_ffmpeg,
    chunk_wav,
    extract_audio_from_mp4,
    download_audio_stream,
    ensure_wav,
    get_wav_duration_seconds,
)


class TestCheckFfmpeg:
    def test_returns_true_when_ffmpeg_available(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, b"", b"")
        )
        assert check_ffmpeg() is True

    def test_returns_false_when_ffmpeg_missing(self, monkeypatch):
        def raise_fnf(*args, **kwargs):
            raise FileNotFoundError("ffmpeg not found")

        monkeypatch.setattr(subprocess, "run", raise_fnf)
        assert check_ffmpeg() is False


class TestExtractAudioFromMp4:
    def test_calls_ffmpeg_with_correct_args(self, monkeypatch, tmp_path):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            out_path = Path(cmd[-1])
            out_path.touch()
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        mp4 = tmp_path / "input.mp4"
        mp4.touch()
        wav = tmp_path / "output.wav"

        extract_audio_from_mp4(mp4, wav)

        cmd = calls[0]
        assert "ffmpeg" in cmd[0]
        assert "-ar" in cmd and "16000" in cmd
        assert "-ac" in cmd and "1" in cmd
        assert str(wav) in cmd

    def test_raises_if_ffmpeg_fails(self, monkeypatch, tmp_path):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, b"", b"err")

        monkeypatch.setattr(subprocess, "run", fake_run)

        mp4 = tmp_path / "input.mp4"
        wav = tmp_path / "output.wav"

        with pytest.raises(RuntimeError, match="ffmpeg"):
            extract_audio_from_mp4(mp4, wav)


class TestDownloadAudioStream:
    def test_calls_curl_with_cookie_and_referer(self, monkeypatch, tmp_path):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            out_path = Path(cmd[cmd.index("-o") + 1])
            out_path.write_bytes(b"fake audio bytes")
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        url = "https://example.com/audio.m4s"
        out = tmp_path / "audio.m4s"

        download_audio_stream(url, out, cookie="SESSDATA=test")

        cmd = calls[0]
        assert "curl" in cmd[0]
        assert any("Cookie: SESSDATA=test" in str(c) for c in cmd)
        assert any("Referer: https://www.bilibili.com" in str(c) for c in cmd)
        assert url in cmd


class TestEnsureWav:
    def test_skips_if_wav_already_exists(self, tmp_path):
        wav = tmp_path / "test.wav"
        wav.touch()
        assert ensure_wav(source=wav, output_dir=tmp_path) == wav


class TestGetWavDuration:
    def test_parses_duration_from_ffprobe(self, monkeypatch, tmp_path):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, b"2694.373875\n", b"")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert get_wav_duration_seconds(tmp_path / "fake.wav") == 2694.373875

    def test_raises_on_ffprobe_failure(self, monkeypatch, tmp_path):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, b"", b"err")

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(RuntimeError, match="ffprobe"):
            get_wav_duration_seconds(tmp_path / "fake.wav")


class TestChunkWav:
    def test_returns_chunks_with_offsets(self, monkeypatch, tmp_path):
        def fake_run(cmd, **kwargs):
            # 创建模拟的 chunk 文件
            for i in range(3):
                (tmp_path / f"input_chunk_{i:04d}.wav").touch()
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        wav = tmp_path / "input.wav"
        wav.touch()
        chunks = chunk_wav(wav, tmp_path, chunk_seconds=300)

        assert len(chunks) == 3
        assert [c[1] for c in chunks] == [0.0, 300.0, 600.0]

    def test_raises_on_ffmpeg_failure(self, monkeypatch, tmp_path):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, b"", b"err")

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(RuntimeError, match="ffmpeg"):
            chunk_wav(tmp_path / "x.wav", tmp_path, chunk_seconds=300)
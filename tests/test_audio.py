import subprocess
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from b2text.audio import (
    check_ffmpeg,
    chunk_wav,
    extract_audio_from_mp4,
    download_audio_stream,
    download_audio_stream_candidates,
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
    def test_downloads_with_httpx(self, monkeypatch, tmp_path):
        """下载使用 httpx 而非 curl。"""
        fake_content = b"fake audio bytes"
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.content = fake_content

        mock_client = MagicMock()
        mock_client.__enter__.return_value.get.return_value = mock_response
        mock_client_class = MagicMock(return_value=mock_client)
        monkeypatch.setattr("httpx.Client", mock_client_class)

        url = "https://example.com/audio.m4s"
        out = tmp_path / "audio.m4s"
        out.parent.mkdir(parents=True, exist_ok=True)

        download_audio_stream(url, out, cookie="SESSDATA=test")

        assert out.read_bytes() == fake_content
        # Verify Referer header is set for Bilibili
        _, kwargs = mock_client.__enter__.return_value.get.call_args
        headers = kwargs.get("headers", {})
        assert "Referer: https://www.bilibili.com" in str(headers) or "Referer" in headers

    def test_raises_on_http_error(self, monkeypatch, tmp_path):
        import httpx
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPError("timeout")

        mock_client = MagicMock()
        mock_client.__enter__.return_value.get.return_value = mock_response
        mock_client_class = MagicMock(return_value=mock_client)
        monkeypatch.setattr("httpx.Client", mock_client_class)

        url = "https://example.com/audio.m4s"
        out = tmp_path / "audio.m4s"

        with pytest.raises(RuntimeError, match="音频下载失败"):
            download_audio_stream(url, out, cookie="SESSDATA=test")


class TestDownloadAudioStreamCandidates:
    def _fake_client(self, monkeypatch, fail_urls):
        import httpx

        class FakeClient:
            def __init__(self, **kwargs):
                self.calls = []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def get(self, url, **kwargs):
                self.calls.append(url)
                r = MagicMock()
                if url in fail_urls:
                    r.raise_for_status.side_effect = httpx.HTTPError("503")
                else:
                    r.raise_for_status.return_value = None
                    r.content = b"ok-bytes"
                return r

        fake = FakeClient()
        monkeypatch.setattr("httpx.Client", lambda **kw: fake)
        return fake

    def test_falls_back_to_second_candidate(self, monkeypatch, tmp_path):
        fake = self._fake_client(monkeypatch, fail_urls={"https://cdn1/m.m4s"})
        out = tmp_path / "a.m4s"
        download_audio_stream_candidates(
            ["https://cdn1/m.m4s", "https://cdn2/m.m4s"],
            out,
            cookie="SESSDATA=x",
            attempts_per_url=1,
            gap_seconds=0,
        )
        assert fake.calls == ["https://cdn1/m.m4s", "https://cdn2/m.m4s"]
        assert out.read_bytes() == b"ok-bytes"

    def test_all_fail_raises_with_candidate_count(self, monkeypatch, tmp_path):
        self._fake_client(monkeypatch, fail_urls={"u1", "u2"})
        with pytest.raises(RuntimeError, match="2 个 CDN 地址"):
            download_audio_stream_candidates(
                ["u1", "u2"],
                tmp_path / "a.m4s",
                cookie="SESSDATA=x",
                attempts_per_url=1,
                gap_seconds=0,
            )

    def test_empty_candidates_raise(self, tmp_path):
        with pytest.raises(RuntimeError, match="没有可用"):
            download_audio_stream_candidates([], tmp_path / "a.m4s", cookie="SESSDATA=x")


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

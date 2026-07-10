# tests/test_bili_api.py
import json
import subprocess
import pytest
from pathlib import Path
from b2text.bili_api import (
    get_video_info,
    get_audio_url,
    extract_series_videos,
)


class TestGetVideoInfo:
    def test_returns_info_on_success(self, monkeypatch):
        fake_response = json.dumps({
            "code": 0,
            "data": {
                "aid": 12345,
                "title": "测试视频",
                "owner": {"name": "测试UP"},
                "videos": 1,
                "pages": [{"cid": 999, "part": "P1", "page": 1}],
                "ugc_season": None,
            }
        })

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, fake_response.encode(), b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        info = get_video_info("BV1test123")
        assert info is not None
        assert info["title"] == "测试视频"
        assert info["aid"] == 12345
        assert info["pages"][0]["cid"] == 999

    def test_returns_none_on_error_response(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, b'{"code": -1}', b"")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert get_video_info("BV1test") is None

    def test_returns_none_on_network_failure(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 7, b"", b"network error")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert get_video_info("BV1test") is None


class TestGetAudioUrl:
    def test_returns_audio_url(self, monkeypatch):
        fake_response = json.dumps({
            "code": 0,
            "data": {
                "dash": {
                    "audio": [{"baseUrl": "https://example.com/audio.m4s"}],
                    "video": [{"baseUrl": "https://example.com/video.m4s"}],
                }
            }
        })

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, fake_response.encode(), b"")

        monkeypatch.setattr(subprocess, "run", fake_run)
        url = get_audio_url(aid=12345, cid=999)
        assert url == "https://example.com/audio.m4s"

    def test_returns_none_when_no_audio(self, monkeypatch):
        fake_response = json.dumps({
            "code": 0,
            "data": {"dash": {"video": [], "audio": []}}
        })

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, fake_response.encode(), b"")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert get_audio_url(aid=1, cid=1) is None


class TestExtractSeriesVideos:
    def test_extracts_from_ugc_season(self):
        season = {
            "sections": [
                {
                    "episodes": [
                        {"bvid": "BV1aaa", "title": "EP1", "cid": 100},
                        {"bvid": "BV1bbb", "title": "EP2", "cid": 200},
                    ]
                }
            ]
        }
        videos = extract_series_videos(season)
        assert len(videos) == 2
        assert videos[0]["bvid"] == "BV1aaa"
        assert videos[1]["cid"] == 200

    def test_empty_season(self):
        assert extract_series_videos({"sections": []}) == []
        assert extract_series_videos(None) == []

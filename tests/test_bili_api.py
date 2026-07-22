import pytest
from unittest.mock import MagicMock
from b2text.bili_api import (
    get_video_info,
    get_audio_url,
)


class TestGetVideoInfo:
    def _mock_httpx_get(self, monkeypatch, status_code=200, json_data=None):
        mock_response = MagicMock()
        mock_response.status_code = status_code
        mock_response.json.return_value = json_data or {}
        mock_client = MagicMock()
        mock_client.__enter__.return_value.get.return_value = mock_response
        mock_client_class = MagicMock(return_value=mock_client)
        monkeypatch.setattr("httpx.Client", mock_client_class)
        return mock_response

    def test_returns_info_on_success(self, monkeypatch):
        fake_data = {
            "code": 0,
            "data": {
                "aid": 12345,
                "title": "测试视频",
                "owner": {"name": "测试UP"},
                "videos": 1,
                "pages": [{"cid": 999, "part": "P1", "page": 1}],
                "ugc_season": None,
            }
        }
        self._mock_httpx_get(monkeypatch, json_data=fake_data)

        info = get_video_info("BV1test123", cookie="SESSDATA=test")
        assert info is not None
        assert info["title"] == "测试视频"
        assert info["aid"] == 12345
        assert info["pages"][0]["cid"] == 999

    def test_returns_none_on_error_response(self, monkeypatch):
        self._mock_httpx_get(monkeypatch, json_data={"code": -1})
        assert get_video_info("BV1test", cookie="SESSDATA=test") is None

    def test_returns_none_on_network_failure(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.__enter__.return_value.get.side_effect = ConnectionError("network error")
        mock_client_class = MagicMock(return_value=mock_client)
        monkeypatch.setattr("httpx.Client", mock_client_class)
        assert get_video_info("BV1test", cookie="SESSDATA=test") is None


class TestGetAudioUrl:
    def _mock_httpx_get(self, monkeypatch, json_data=None):
        mock_response = MagicMock()
        mock_response.json.return_value = json_data or {}
        mock_client = MagicMock()
        mock_client.__enter__.return_value.get.return_value = mock_response
        mock_client_class = MagicMock(return_value=mock_client)
        monkeypatch.setattr("httpx.Client", mock_client_class)

    def test_returns_audio_url(self, monkeypatch):
        fake_data = {
            "code": 0,
            "data": {
                "dash": {
                    "audio": [{"baseUrl": "https://example.com/audio.m4s"}],
                    "video": [{"baseUrl": "https://example.com/video.m4s"}],
                }
            }
        }
        self._mock_httpx_get(monkeypatch, json_data=fake_data)
        url = get_audio_url(aid=12345, cid=999, cookie="SESSDATA=test")
        assert url == "https://example.com/audio.m4s"

    def test_returns_none_when_no_audio(self, monkeypatch):
        fake_data = {
            "code": 0,
            "data": {"dash": {"video": [], "audio": []}}
        }
        self._mock_httpx_get(monkeypatch, json_data=fake_data)
        assert get_audio_url(aid=1, cid=1, cookie="SESSDATA=test") is None

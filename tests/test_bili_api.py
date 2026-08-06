import pytest
from unittest.mock import MagicMock
from b2text.bili_api import (
    get_video_info,
    get_audio_url,
    get_audio_urls,
    extract_series_videos,
    BiliAPIError,
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

    def test_raises_bili_api_error_on_business_error(self, monkeypatch):
        """B 站业务错误（code != 0）应抛 BiliAPIError 并透出 code/message。"""
        self._mock_httpx_get(monkeypatch, json_data={
            "code": -352, "message": "请求被拦截",
        })
        with pytest.raises(BiliAPIError) as exc_info:
            get_video_info("BV1test", cookie="SESSDATA=test")
        assert exc_info.value.code == -352
        assert "请求被拦截" in str(exc_info.value)

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

    def test_raises_bili_api_error_on_business_error(self, monkeypatch):
        self._mock_httpx_get(monkeypatch, json_data={
            "code": -404, "message": "稿件不存在",
        })
        with pytest.raises(BiliAPIError) as exc_info:
            get_audio_url(aid=1, cid=1, cookie="SESSDATA=test")
        assert exc_info.value.code == -404
        assert "稿件不存在" in str(exc_info.value)

    def test_get_audio_urls_includes_backup_dedup(self, monkeypatch):
        """候选 = baseUrl + backupUrl，去重保序。"""
        fake_data = {"code": 0, "data": {"dash": {"audio": [
            {
                "baseUrl": "https://cdn-a.m4s",
                "backupUrl": ["https://cdn-b.m4s", "https://cdn-b.m4s"],
                "backup_url": ["https://cdn-c.m4s"],
            }
        ]}}}
        self._mock_httpx_get(monkeypatch, json_data=fake_data)
        assert get_audio_urls(aid=1, cid=1, cookie="SESSDATA=test") == [
            "https://cdn-a.m4s",
            "https://cdn-b.m4s",
            "https://cdn-c.m4s",
        ]

    def test_get_audio_urls_empty_when_no_audio(self, monkeypatch):
        self._mock_httpx_get(monkeypatch, json_data={
            "code": 0, "data": {"dash": {"audio": []}},
        })
        assert get_audio_urls(aid=1, cid=1, cookie="SESSDATA=test") == []


class TestExtractSeriesVideos:
    def test_none_returns_empty(self):
        assert extract_series_videos(None) == []
        assert extract_series_videos({}) == []

    def test_extracts_episodes_across_sections(self):
        ugc = {"sections": [
            {"episodes": [
                {"bvid": "BV1a", "title": "第一集", "cid": 101, "aid": 1},
                {"bvid": "BV1b", "title": "第二集", "cid": 102, "aid": 2},
            ]},
            {"episodes": [
                {"bvid": "BV1c", "title": "第三集", "cid": 103, "aid": 3},
            ]},
        ]}
        out = extract_series_videos(ugc)
        assert [v["bvid"] for v in out] == ["BV1a", "BV1b", "BV1c"]
        assert out[0]["title"] == "第一集"
        assert out[0]["cid"] == 101
        assert out[0]["aid"] == 1

    def test_skips_episodes_without_bvid(self):
        ugc = {"sections": [{"episodes": [
            {"title": "无 BV", "cid": 1},
            {"bvid": "BV1ok", "title": "正常", "cid": 2, "aid": 9},
        ]}]}
        assert [v["bvid"] for v in extract_series_videos(ugc)] == ["BV1ok"]


class TestRateLimiting:
    """Both get_video_info and get_audio_url must consult the shared B站 bucket."""

    def _mock_httpx_get(self, monkeypatch):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "code": 0,
            "data": {
                "aid": 1, "title": "t", "owner": {"name": "u"},
                "videos": 1, "pages": [{"cid": 1, "part": "p", "page": 1}],
                "ugc_season": None,
            },
        }
        mock_client = MagicMock()
        mock_client.__enter__.return_value.get.return_value = mock_response
        mock_client_class = MagicMock(return_value=mock_client)
        monkeypatch.setattr("httpx.Client", mock_client_class)
        return mock_client_class

    def test_get_video_info_acquires_bucket_token(self, monkeypatch):
        """`_api_get` 必须调用 `_BILI_BUCKET.acquire()`，否则 B 站会 429。"""
        from b2text import bili_api
        calls = []
        monkeypatch.setattr(bili_api._BILI_BUCKET, "acquire", lambda: calls.append(1))
        self._mock_httpx_get(monkeypatch)
        get_video_info(bvid="BV1xxx", cookie="SESSDATA=test")
        assert len(calls) == 1

    def test_get_audio_url_acquires_bucket_token(self, monkeypatch):
        from b2text import bili_api
        calls = []
        monkeypatch.setattr(bili_api._BILI_BUCKET, "acquire", lambda: calls.append(1))
        self._mock_httpx_get(monkeypatch)
        get_audio_url(aid=1, cid=1, cookie="SESSDATA=test")
        assert len(calls) == 1

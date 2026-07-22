# tests/test_upmaster.py
import pytest
import httpx
from unittest.mock import MagicMock
from b2text.upmaster import fetch_up_videos, UpmasterAPIError


def _mock_httpx_get(monkeypatch, json_data=None):
    mock_response = MagicMock()
    mock_response.json.return_value = json_data or {}
    mock_client = MagicMock()
    mock_client.__enter__.return_value.get.return_value = mock_response
    mock_client_class = MagicMock(return_value=mock_client)
    monkeypatch.setattr("httpx.Client", mock_client_class)
    return mock_response


def test_returns_bvid_list_within_limit(monkeypatch):
    """调用 B 站 space/arc/search，返回 bvid 列表。"""
    fake = {
        "code": 0,
        "data": {
            "list": {
                "vlist": [
                    {"bvid": "BV1aaa"}, {"bvid": "BV1bbb"}, {"bvid": "BV1ccc"},
                ]
            }
        }
    }
    _mock_httpx_get(monkeypatch, json_data=fake)

    bvids = fetch_up_videos(uid=12345, limit=2, cookie="SESSDATA=x")
    assert bvids == ["BV1aaa", "BV1bbb"]


def test_clamps_limit_to_max_50(monkeypatch):
    """B 站单页最多 50，limit > 50 时取 50。"""
    captured = {}

    def capturing_get(url, **kwargs):
        captured["url"] = url
        mock_response = MagicMock()
        mock_response.json.return_value = {"code": 0, "data": {"list": {"vlist": []}}}
        return mock_response

    mock_client = MagicMock()
    mock_client.__enter__.return_value.get.side_effect = capturing_get
    mock_client_class = MagicMock(return_value=mock_client)
    monkeypatch.setattr("httpx.Client", mock_client_class)

    fetch_up_videos(uid=1, limit=10, cookie="SESSDATA=x")
    assert "ps=10" in captured["url"]

    fetch_up_videos(uid=1, limit=999, cookie="SESSDATA=x")
    assert "ps=50" in captured["url"]  # 最大 50


def test_raises_on_api_error(monkeypatch):
    """B 站返回 code != 0 时抛 UpmasterAPIError，透传 code 和 message。"""
    _mock_httpx_get(monkeypatch, json_data={
        "code": -799, "message": "请求过于频繁，请稍后再试",
    })
    with pytest.raises(UpmasterAPIError) as exc_info:
        fetch_up_videos(uid=486325909, limit=10, cookie="SESSDATA=x")
    assert exc_info.value.code == -799
    assert "请求过于频繁" in exc_info.value.message
    assert exc_info.value.uid == 486325909
    assert "-799" in str(exc_info.value)
    assert "请求过于频繁" in str(exc_info.value)


def test_raises_on_http_error(monkeypatch):
    """HTTP/JSON 解析失败也抛 UpmasterAPIError，code 标记为 -1。"""

    def raising_get(url, **kwargs):
        raise httpx.ReadTimeout("timed out")

    mock_client = MagicMock()
    mock_client.__enter__.return_value.get.side_effect = raising_get
    monkeypatch.setattr("httpx.Client", MagicMock(return_value=mock_client))

    with pytest.raises(UpmasterAPIError) as exc_info:
        fetch_up_videos(uid=1, limit=10, cookie="SESSDATA=x")
    assert exc_info.value.code == -1
    assert "ReadTimeout" in exc_info.value.message


def test_returns_empty_when_vlist_actually_empty(monkeypatch):
    """API 成功但 vlist 为空（UP 主真的没视频）→ 返回 []，不抛异常。"""
    _mock_httpx_get(monkeypatch, json_data={
        "code": 0, "data": {"list": {"vlist": []}},
    })
    assert fetch_up_videos(uid=1, limit=10, cookie="SESSDATA=x") == []


def test_passes_cookie_header(monkeypatch):
    captured = {}

    def capturing_get(url, **kwargs):
        captured["headers"] = kwargs.get("headers", {})
        mock_response = MagicMock()
        mock_response.json.return_value = {"code": 0, "data": {"list": {"vlist": []}}}
        return mock_response

    mock_client = MagicMock()
    mock_client.__enter__.return_value.get.side_effect = capturing_get
    mock_client_class = MagicMock(return_value=mock_client)
    monkeypatch.setattr("httpx.Client", mock_client_class)

    fetch_up_videos(uid=99, limit=5, cookie="SESSDATA=my_cookie")
    assert "SESSDATA=my_cookie" in captured["headers"].get("Cookie", "")

# tests/test_upmaster.py
import pytest
import httpx
from unittest.mock import MagicMock
from b2text.upmaster import fetch_up_videos, UpmasterAPIError


@pytest.fixture(autouse=True)
def _fake_wbi_signature(monkeypatch):
    """wbi 签名需要网络拉取 img/sub key；单测里换成固定签名，避免真实请求。"""
    import time

    def fake_sign(params, *, cookie, ua):
        return {**params, "wts": str(int(time.time())), "w_rid": "0" * 32}

    monkeypatch.setattr("b2text.upmaster.sign_query", fake_sign)


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


def test_clamps_page_size_to_max_50(monkeypatch):
    """B 站 wbi/arc/search 单页最多 50；limit > 50 时取 50。"""
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

    # 用的是 wbi/arc/search（新 endpoint），不是旧的 arc/search
    assert "wbi/arc/search" in captured["url"]


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


def test_acquires_bucket_token_before_request(monkeypatch):
    """fanout 调用 B 站 API，必须经过共享 _BILI_BUCKET 限速。"""
    from b2text import upmaster
    calls = []
    monkeypatch.setattr(upmaster._BILI_BUCKET, "acquire", lambda: calls.append(1))
    _mock_httpx_get(monkeypatch, json_data={
        "code": 0, "data": {"list": {"vlist": [{"bvid": "BV1a"}]}},
    })
    fetch_up_videos(uid=1, limit=1, cookie="SESSDATA=x")
    # 拿够 limit 就停
    assert len(calls) == 1


def test_paginates_when_limit_exceeds_page_size(monkeypatch):
    """--limit > 50 时应翻页（pn=1,2,...）直到拿够 limit 条。"""
    from b2text import upmaster
    monkeypatch.setattr(upmaster._BILI_BUCKET, "acquire", lambda: None)

    # 总共 130 条：前 50 / 中 50 / 后 30
    all_bvids = (
        [f"BV1p{i:02d}" for i in range(50)]
        + [f"BV1q{i:02d}" for i in range(50)]
        + [f"BV1r{i:02d}" for i in range(30)]
    )
    cursor = {"i": 0}
    captured_pn: list[int] = []

    def fake_get(url, **kwargs):
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(url).query)
        pn = int(qs["pn"][0])
        ps = int(qs["ps"][0])
        captured_pn.append(pn)
        start = cursor["i"]
        chunk = all_bvids[start:start + ps]
        cursor["i"] += len(chunk)
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "code": 0,
            "data": {"list": {"vlist": [{"bvid": b} for b in chunk]}},
        }
        return mock_response

    mock_client = MagicMock()
    mock_client.__enter__.return_value.get.side_effect = fake_get
    monkeypatch.setattr("httpx.Client", MagicMock(return_value=mock_client))

    bvids = fetch_up_videos(uid=1, limit=130, cookie="SESSDATA=x")
    assert len(bvids) == 130
    assert captured_pn == [1, 2, 3]


def test_stops_paginating_when_b_station_returns_empty(monkeypatch):
    """B 站某页返回空 vlist → 停止翻页（UP 主没更多视频了）。"""
    from b2text import upmaster
    monkeypatch.setattr(upmaster._BILI_BUCKET, "acquire", lambda: None)

    # UP 主只有 15 条视频；pn=3 会返回空
    all_bvids = (
        [f"BV1a{i}" for i in range(10)]
        + [f"BV1b{i}" for i in range(5)]
    )
    cursor = {"i": 0}
    call_count = {"n": 0}

    def fake_get(url, **kwargs):
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(url).query)
        ps = int(qs["ps"][0])
        start = cursor["i"]
        chunk = all_bvids[start:start + ps]
        cursor["i"] += len(chunk)
        call_count["n"] += 1
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "code": 0,
            "data": {"list": {"vlist": [{"bvid": b} for b in chunk]}},
        }
        return mock_response

    mock_client = MagicMock()
    mock_client.__enter__.return_value.get.side_effect = fake_get
    monkeypatch.setattr("httpx.Client", MagicMock(return_value=mock_client))

    # 请求 100，但 UP 主只有 15 条 — 应在 pn=2 看到空后停止
    bvids = fetch_up_videos(uid=1, limit=100, cookie="SESSDATA=x")
    assert len(bvids) == 15
    # ps=50, pn=1 返回 15 (一次拿完); pn=2 会读到 []. 所以 2 次调用足够。
    assert call_count["n"] == 2


def test_limit_smaller_than_page_size_still_single_request(monkeypatch):
    """limit <= 10 时只发一次请求（pn=1）。"""
    from b2text import upmaster
    monkeypatch.setattr(upmaster._BILI_BUCKET, "acquire", lambda: None)

    captured_pn: list[int] = []

    def fake_get(url, **kwargs):
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(url).query)
        captured_pn.append(int(qs["pn"][0]))
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "code": 0,
            "data": {"list": {"vlist": [{"bvid": f"BV1a{i}"} for i in range(10)]}},
        }
        return mock_response

    mock_client = MagicMock()
    mock_client.__enter__.return_value.get.side_effect = fake_get
    monkeypatch.setattr("httpx.Client", MagicMock(return_value=mock_client))

    bvids = fetch_up_videos(uid=1, limit=10, cookie="SESSDATA=x")
    assert len(bvids) == 10
    assert captured_pn == [1]

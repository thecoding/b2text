# tests/test_upmaster.py
import json
import subprocess
import pytest
from b2text.upmaster import fetch_up_videos


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

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, json.dumps(fake).encode(), b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    bvids = fetch_up_videos(uid=12345, limit=2, cookie="SESSDATA=x")
    assert bvids == ["BV1aaa", "BV1bbb"]


def test_clamps_limit_to_max_50(monkeypatch):
    """B 站单页最多 50，limit > 50 时取 50。"""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"code":0,"data":{"list":{"vlist":[]}}}).encode(), b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    fetch_up_videos(uid=1, limit=10, cookie="SESSDATA=x")
    cmd_str = " ".join(captured["cmd"])
    assert "ps=10" in cmd_str

    fetch_up_videos(uid=1, limit=999, cookie="SESSDATA=x")
    cmd_str = " ".join(captured["cmd"])
    assert "ps=50" in cmd_str  # 最大 50


def test_returns_empty_on_error(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, b'{"code":-1}', b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert fetch_up_videos(uid=1, limit=10, cookie="SESSDATA=x") == []


def test_passes_cookie_header(monkeypatch):
    fake = {"code": 0, "data": {"list": {"vlist": []}}}
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, json.dumps(fake).encode(), b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    fetch_up_videos(uid=99, limit=5, cookie="SESSDATA=my_cookie")
    cmd = captured["cmd"]
    assert any("Cookie: SESSDATA=my_cookie" in str(c) for c in cmd)

from unittest.mock import patch, MagicMock
from b2text.client import (
    DaemonClient, DaemonNotRunning, submit_bv, submit_up, get_task, list_tasks, cancel_task,
)


def test_daemon_not_running_raises(monkeypatch):
    def boom(*a, **kw):
        import httpx
        raise httpx.ConnectError("connection refused")
    monkeypatch.setattr("httpx.get", boom)
    with patch("httpx.post", boom):
        with patch("httpx.delete", boom):
            client = DaemonClient("http://127.0.0.1:8765")
            with __import__("pytest").raises(DaemonNotRunning):
                client.health()


def test_submit_bv_posts_bv_payload(monkeypatch):
    posted = {}
    def fake_post(url, json=None, **kw):
        posted["url"] = url
        posted["json"] = json
        class Resp:
            status_code = 200
            def json(self):
                return {"task_id": "abc", "skipped": False}
            def raise_for_status(self):
                pass
        return Resp()
    monkeypatch.setattr("httpx.post", fake_post)
    resp = submit_bv("http://127.0.0.1:8765", "BV1xxx", "/tmp/out", force=True)
    assert resp["task_id"] == "abc"
    assert resp["skipped"] is False
    assert posted["json"]["type"] == "bv"
    assert posted["json"]["id"] == "BV1xxx"
    assert posted["json"]["force"] is True


def test_submit_up_posts_up_payload(monkeypatch):
    posted = {}
    def fake_post(url, json=None, **kw):
        posted["json"] = json
        class Resp:
            status_code = 200
            def json(self): return {"task_id": "xyz"}
            def raise_for_status(self): pass
        return Resp()
    monkeypatch.setattr("httpx.post", fake_post)
    task_id = submit_up("http://127.0.0.1:8765", "12345", "/tmp/out", limit=10)
    assert task_id == "xyz"
    assert posted["json"]["type"] == "up"
    assert posted["json"]["limit"] == 10


def test_get_task_returns_dict(monkeypatch):
    def fake_get(url, **kw):
        class Resp:
            status_code = 200
            def json(self): return {"id": "abc", "status": "running"}
            def raise_for_status(self): pass
        return Resp()
    monkeypatch.setattr("httpx.get", fake_get)
    job = get_task("http://127.0.0.1:8765", "abc")
    assert job["id"] == "abc"
    assert job["status"] == "running"


def test_cancel_calls_delete(monkeypatch):
    called = {}
    def fake_delete(url, **kw):
        called["url"] = url
        class Resp:
            status_code = 200
            def raise_for_status(self): pass
        return Resp()
    monkeypatch.setattr("httpx.delete", fake_delete)
    cancel_task("http://127.0.0.1:8765", "abc")
    assert called["url"].endswith("/tasks/abc")

import pytest
from fastapi.testclient import TestClient
from b2text.server import build_app, AppContext


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("B2TEXT_COOKIE", "SESSDATA=test")
    db = tmp_path / "jobs.db"
    log = tmp_path / "jobs.log"

    ctx = AppContext(
        db_path=db,
        log_path=log,
        cookie="SESSDATA=test",
        run_real_pipeline=False,
    )
    app = build_app(ctx)
    return TestClient(app)


def test_health_ok_when_pipeline_disabled(app):
    r = app.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert "ok" in body
    assert "model_loaded" in body


def test_post_transcribe_returns_task_id(app):
    r = app.post("/transcribe", json={
        "type": "bv", "id": "BV1xxx", "output_dir": "/tmp/out"
    })
    assert r.status_code == 200
    body = r.json()
    assert "task_id" in body
    assert len(body["task_id"]) > 0


def test_post_transcribe_rejects_bad_id(app):
    r = app.post("/transcribe", json={"type": "bv", "id": "not-a-bvid", "output_dir": "/tmp/out"})
    assert r.status_code == 400


def test_get_task_returns_status(app):
    r = app.post("/transcribe", json={"type": "bv", "id": "BV1aaa", "output_dir": "/tmp/out"})
    task_id = r.json()["task_id"]
    r = app.get(f"/tasks/{task_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == task_id
    assert body["type"] == "bv"


def test_get_task_log_returns_lines(app):
    r = app.post("/transcribe", json={"type": "bv", "id": "BV1aaa", "output_dir": "/tmp/out"})
    task_id = r.json()["task_id"]
    ctx_log = app.app.state.ctx.log_path
    ctx_log.parent.mkdir(parents=True, exist_ok=True)
    ctx_log.write_text(
        '{"ts":"2026-07-14T12:00:00.000Z","level":"INFO","job_id":"' + task_id + '","step":"_","msg":"hi","extra":{}}\n',
        encoding="utf-8",
    )
    r = app.get(f"/tasks/{task_id}/log")
    assert r.status_code == 200
    body = r.json()
    assert "logs" in body
    assert len(body["logs"]) >= 1


def test_delete_task_only_cancels_queued(app):
    r = app.post("/transcribe", json={"type": "bv", "id": "BV1xxx", "output_dir": "/tmp/out"})
    task_id = r.json()["task_id"]
    r = app.delete(f"/tasks/{task_id}")
    assert r.status_code == 200
    assert "cancelled" in app.get(f"/tasks/{task_id}").json()["status"]


def test_list_tasks_returns_array(app):
    app.post("/transcribe", json={"type": "bv", "id": "BV1a", "output_dir": "/tmp/out"})
    app.post("/transcribe", json={"type": "bv", "id": "BV1b", "output_dir": "/tmp/out"})
    r = app.get("/tasks")
    assert r.status_code == 200
    assert "tasks" in r.json()
    assert len(r.json()["tasks"]) >= 2
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
    # 日志现在从 SQLite job_logs 表读取；通过 get_queue 依赖写入一条
    from b2text.queue import JobQueue
    q = JobQueue(app.app.state.ctx.db_path)
    try:
        q.append_log(task_id, '{"ts":"2026-07-14T12:00:00.000Z","level":"INFO","job_id":"' + task_id + '","step":"_","msg":"hi","extra":{}}')
    finally:
        q.close()
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
def test_health_includes_queue_metrics(app):
    """健康检查返回队列长度和运行中任务数（基于 count()）。"""
    r = app.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert "queue_len" in body
    assert "running" in body
    assert isinstance(body["queue_len"], int)
    assert isinstance(body["running"], int)

    # 入队后 queue_len 应增加
    app.post("/transcribe", json={
        "type": "bv", "id": "BV1xxx", "output_dir": "/tmp/out"
    })
    r2 = app.get("/health")
    assert r2.json()["queue_len"] >= 1


def test_health_shows_model_loaded_when_pipeline_disabled(app):
    """--no-funasr 模式下 model_loaded=True。"""
    r = app.get("/health")
    assert r.json()["model_loaded"] is True


def test_list_tasks_filters_by_status(app):
    app.post("/transcribe", json={
        "type": "bv", "id": "BV1done", "output_dir": "/tmp/out"
    })
    app.post("/transcribe", json={
        "type": "bv", "id": "BV1queued", "output_dir": "/tmp/out"
    })
    r = app.get("/tasks?status=queued")
    assert r.status_code == 200
    body = r.json()
    assert len(body["tasks"]) == 2
    for t in body["tasks"]:
        assert t["status"] == "queued"
    # total 使用 count()，不应受 limit 截断
    assert body["total"] == 2


def test_list_tasks_includes_progress(app):
    """每条任务附带 progress={step, msg}（来自 jobs.log 最后一行）；无日志时为 None。"""
    r = app.post("/transcribe", json={
        "type": "bv", "id": "BV1prog", "output_dir": "/tmp/out"
    })
    task_id = r.json()["task_id"]

    # 还没写过日志 → progress 应为 None
    body = app.get("/tasks").json()
    target = next(t for t in body["tasks"] if t["id"] == task_id)
    assert target["progress"] is None

    # 写入一条 start 日志 → progress 应反映
    log_path = app.app.state.ctx.log_path
    log_path.parent.mkdir(parents=True, exist_ok=True)
    line = (
        '{"ts":"2026-07-22T12:00:00.000Z","level":"INFO",'
        f'"job_id":"{task_id}","step":"transcribe","msg":"start","extra":{{}}}}'
    )
    log_path.write_text(line + "\n", encoding="utf-8")

    body = app.get("/tasks").json()
    target = next(t for t in body["tasks"] if t["id"] == task_id)
    assert target["progress"] == {"step": "transcribe", "msg": "start"}

    # 追加 fail 日志 → progress 应更新到最新
    fail_line = (
        '{"ts":"2026-07-22T12:00:01.000Z","level":"ERROR",'
        f'"job_id":"{task_id}","step":"transcribe","msg":"fail","extra":{{}}}}'
    )
    with log_path.open("a", encoding="utf-8") as f:
        f.write(fail_line + "\n")

    body = app.get("/tasks").json()
    target = next(t for t in body["tasks"] if t["id"] == task_id)
    assert target["progress"] == {"step": "transcribe", "msg": "fail"}


def test_submit_up_rejects_invalid_id(app):
    """type=up 的 id 必须为纯数字。"""
    r = app.post("/transcribe", json={
        "type": "up", "id": "not-a-number", "output_dir": "/tmp/out"
    })
    assert r.status_code == 400


def test_submit_up_accepts_valid_uid(app):
    r = app.post("/transcribe", json={
        "type": "up", "id": "12345", "output_dir": "/tmp/out", "limit": 10
    })
    assert r.status_code == 200
    task_id = r.json()["task_id"]
    r2 = app.get(f"/tasks/{task_id}")
    assert r2.json()["type"] == "up"


def test_cancel_nonexistent_task_returns_404(app):
    r = app.delete("/tasks/nonexistent-id")
    assert r.status_code == 404


def test_get_nonexistent_task_returns_404(app):
    r = app.get("/tasks/nonexistent-id")
    assert r.status_code == 404


def test_get_log_nonexistent_task_returns_404(app):
    r = app.get("/tasks/nonexistent-id/log")
    assert r.status_code == 404


def test_list_tasks_supports_pagination(app):
    for i in range(5):
        app.post("/transcribe", json={
            "type": "bv", "id": f"BV1test{i}", "output_dir": "/tmp/out"
        })
    r = app.get("/tasks?limit=2&offset=0")
    body = r.json()
    assert len(body["tasks"]) == 2
    assert body["total"] >= 5
    r2 = app.get("/tasks?limit=2&offset=2")
    assert len(r2.json()["tasks"]) == 2

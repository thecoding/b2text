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
    assert body["skipped"] is False


def test_transcribe_dedup_inflight(app):
    """同一 BV 已排队/运行中时，再次提交复用原任务，不重复入队。"""
    from b2text.queue import JobQueue, JobStatus
    r1 = app.post("/transcribe", json={
        "type": "bv", "id": "BV1dup", "output_dir": "/tmp/out"
    })
    r2 = app.post("/transcribe", json={
        "type": "bv", "id": "BV1dup", "output_dir": "/tmp/out"
    })
    b1, b2 = r1.json(), r2.json()
    assert b1["task_id"] == b2["task_id"]
    assert b2["skipped"] is True
    assert b2["reason"] == "in_progress"
    q = JobQueue(app.app.state.ctx.db_path)
    try:
        assert q.count(status=JobStatus.QUEUED) == 1
    finally:
        q.close()


def test_transcribe_dedup_done_with_existing_file(app, tmp_path):
    """任务已成功且结果文件仍在时，再次提交复用原任务。"""
    from b2text.queue import JobQueue
    out = tmp_path / "out"
    out.mkdir()
    r1 = app.post("/transcribe", json={
        "type": "bv", "id": "BV1dup", "output_dir": str(out)
    })
    task_id = r1.json()["task_id"]
    result = out / "BV1dup.txt"
    result.write_text("hello", encoding="utf-8")
    q = JobQueue(app.app.state.ctx.db_path)
    try:
        q.claim_next()
        q.finish(task_id, result_path=str(result))
    finally:
        q.close()

    r2 = app.post("/transcribe", json={
        "type": "bv", "id": "BV1dup", "output_dir": str(out)
    })
    body = r2.json()
    assert body["skipped"] is True
    assert body["reason"] == "already_exists"
    assert body["task_id"] == task_id
    assert body["result_path"] == str(result)


def test_transcribe_reruns_when_result_file_deleted(app, tmp_path):
    """结果文件被删后再次提交应重新解析（不复用 done 记录）。"""
    from b2text.queue import JobQueue
    out = tmp_path / "out"
    out.mkdir()
    r1 = app.post("/transcribe", json={
        "type": "bv", "id": "BV1gone", "output_dir": str(out)
    })
    task_id = r1.json()["task_id"]
    result = out / "BV1gone.txt"
    result.write_text("old", encoding="utf-8")
    q = JobQueue(app.app.state.ctx.db_path)
    try:
        q.claim_next()
        q.finish(task_id, result_path=str(result))
    finally:
        q.close()
    result.unlink()

    r2 = app.post("/transcribe", json={
        "type": "bv", "id": "BV1gone", "output_dir": str(out)
    })
    body = r2.json()
    assert body["skipped"] is False
    assert body["task_id"] != task_id


def test_transcribe_dedup_bare_file_without_record(app, tmp_path):
    """输出文件已存在但没有任务记录时也跳过（task_id 为空，提示结果路径）。"""
    out = tmp_path / "out"
    out.mkdir()
    result = out / "BV1bare.txt"
    result.write_text("existing", encoding="utf-8")
    r = app.post("/transcribe", json={
        "type": "bv", "id": "BV1bare", "output_dir": str(out)
    })
    body = r.json()
    assert body["skipped"] is True
    assert body["reason"] == "file_exists"
    assert body["task_id"] is None
    assert body["result_path"] == str(result)


def test_transcribe_force_bypasses_dedup(app, tmp_path):
    """force=true 跳过重复检测，即使已有成功结果也重新入队。"""
    from b2text.queue import JobQueue
    out = tmp_path / "out"
    out.mkdir()
    r1 = app.post("/transcribe", json={
        "type": "bv", "id": "BV1redo", "output_dir": str(out)
    })
    task_id = r1.json()["task_id"]
    result = out / "BV1redo.txt"
    result.write_text("old", encoding="utf-8")
    q = JobQueue(app.app.state.ctx.db_path)
    try:
        q.claim_next()
        q.finish(task_id, result_path=str(result))
    finally:
        q.close()

    r2 = app.post("/transcribe", json={
        "type": "bv", "id": "BV1redo", "output_dir": str(out),
        "force": True,
    })
    body = r2.json()
    assert body["skipped"] is False
    assert body["task_id"] != task_id
    assert app.get(f"/tasks/{body['task_id']}").json()["status"] == "queued"


def test_post_transcribe_output_dir_defaults_to_data_dir(app):
    """扩展提交时可不传 output_dir，落到 data_dir/extension。"""
    from b2text.paths import data_dir
    r = app.post("/transcribe", json={"type": "bv", "id": "BV1xxx"})
    assert r.status_code == 200
    job = app.get(f"/tasks/{r.json()['task_id']}").json()
    assert job["output_dir"] == str(data_dir() / "extension")


def test_cors_headers_present(app):
    """本地 daemon 需允许扩展来源（chrome-extension:// Origin）。"""
    r = app.get("/health", headers={"Origin": "chrome-extension://abcdef"})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "*"


def test_get_segments_202_until_done_then_returns_timeline(app):
    """任务未完成时 /segments 返回 202+状态；完成后返回时间线与时长。"""
    from b2text.queue import JobQueue, JobStatus
    r = app.post("/transcribe", json={"type": "bv", "id": "BV1seg", "output_dir": "/tmp/out"})
    task_id = r.json()["task_id"]

    r = app.get(f"/tasks/{task_id}/segments")
    assert r.status_code == 202
    assert r.json()["status"] == "queued"

    q = JobQueue(app.app.state.ctx.db_path)
    try:
        q.finish(task_id, result_path="/tmp/out/BV1seg.txt")
        q.save_segments(task_id, [
            {"start": 0.0, "end": 1.5, "speaker": "Speaker_1", "text": "第一句"},
            {"start": 1.5, "end": 3.0, "speaker": "Speaker_2", "text": "第二句"},
        ])
    finally:
        q.close()

    r = app.get(f"/tasks/{task_id}/segments")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == JobStatus.DONE.value
    assert len(body["segments"]) == 2
    assert body["segments"][0] == {
        "start": 0.0, "end": 1.5, "speaker": "Speaker_1", "text": "第一句",
    }
    assert body["duration"] == 3.0


def test_get_segments_404_for_unknown_task(app):
    assert app.get("/tasks/nope/segments").status_code == 404


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


def test_delete_tasks_by_status(app):
    """DELETE /tasks?status=failed 删除失败任务，body 返回 deleted 数。"""
    for _ in range(2):
        app.post("/transcribe", json={
            "type": "bv", "id": f"BV1xx{_}", "output_dir": "/tmp/out"
        })
    # 标记两条为 failed
    from b2text.queue import JobQueue
    q = JobQueue(app.app.state.ctx.db_path)
    try:
        for jid in [t["id"] for t in q.list()]:
            q.fail(jid, error="boom")
    finally:
        q.close()

    r = app.request("DELETE", "/tasks?status=failed")
    assert r.status_code == 200
    body = r.json()
    assert body["deleted"] == 2


def test_delete_tasks_no_filter_rejected(app):
    """不带任何过滤条件时拒绝（避免误删全部任务）。"""
    r = app.request("DELETE", "/tasks")
    assert r.status_code == 400


def test_delete_tasks_all_clears_everything(app):
    app.post("/transcribe", json={"type": "bv", "id": "BV1a", "output_dir": "/tmp/o"})
    app.post("/transcribe", json={"type": "bv", "id": "BV1b", "output_dir": "/tmp/o"})

    r = app.request("DELETE", "/tasks?all=true")
    assert r.status_code == 200
    assert r.json()["deleted"] == 2
    assert app.get("/tasks").json()["total"] == 0

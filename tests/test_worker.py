import asyncio
import json
import time
from pathlib import Path
import pytest
from unittest.mock import AsyncMock

from b2text.worker import Worker
from b2text.queue import JobQueue, JobStatus


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    db = tmp_path / "jobs.db"
    log = tmp_path / "jobs.log"
    cookie = "SESSDATA=test"
    q = JobQueue(db)
    yield q, log, cookie
    q.close()


def _make_worker(q, log_path, cookie, steps):
    return Worker(queue=q, log_path=log_path, cookie=cookie, steps=steps)


def test_worker_processes_queued_job_to_done(env):
    q, log_path, cookie = env
    steps = {
        "get_video_info": lambda job, log: log.step_ok("get_video_info", aid=12345),
    }
    worker = _make_worker(q, log_path, cookie, steps)
    job_id = q.enqueue(type="bv", target_id="BV1xxx", output_dir="/tmp/out")

    asyncio.run(worker.run_once())

    job = q.get(job_id)
    assert job["status"] == JobStatus.DONE
    assert job["result_path"] == "/tmp/out/BV1xxx.txt"


def test_worker_marks_failed_on_exception(env):
    q, log_path, cookie = env
    def boom(job, log):
        raise RuntimeError("transcribe crashed")
    steps = {"get_video_info": boom}
    worker = _make_worker(q, log_path, cookie, steps)
    job_id = q.enqueue(type="bv", target_id="BV1xxx", output_dir="/tmp/out")

    asyncio.run(worker.run_once())

    job = q.get(job_id)
    assert job["status"] == JobStatus.FAILED
    assert "transcribe crashed" in job["error"]


def test_worker_logs_each_step(env):
    q, log_path, cookie = env
    steps = {
        "get_video_info": lambda job, log: log.step_ok("get_video_info", aid=1),
        "transcribe": lambda job, log: log.step_ok("transcribe", segment_count=42),
    }
    worker = _make_worker(q, log_path, cookie, steps)
    job_id = q.enqueue(type="bv", target_id="BV1xxx", output_dir="/tmp/out")
    asyncio.run(worker.run_once())

    lines = [json.loads(ln) for ln in log_path.read_text().splitlines()]
    steps_seen = [ln["step"] for ln in lines]
    assert "get_video_info" in steps_seen
    assert "transcribe" in steps_seen
    ok_lines = [ln for ln in lines if ln["msg"] == "ok"]
    assert any(ln["extra"].get("aid") == 1 for ln in ok_lines)
    assert any(ln["extra"].get("segment_count") == 42 for ln in ok_lines)


def test_worker_run_once_returns_none_when_queue_empty(env):
    q, log_path, cookie = env
    worker = _make_worker(q, log_path, cookie, steps={"get_video_info": lambda job, log: None})
    assert asyncio.run(worker.run_once()) is None


def test_worker_up_job_finishes_without_result_path(env):
    """type=up 父任务 fan-out 后直接 done，result_path=None。"""
    q, log_path, cookie = env

    def fanout(job, log):
        # 模拟 upmaster 拉到的子 bvid
        for bvid in ["BV1aaa", "BV1bbb"]:
            q.enqueue(type="bv", target_id=bvid, output_dir=job["output_dir"], parent_id=job["id"])
        log.set(child_count=2)

    worker = _make_worker(q, log_path, cookie, steps={"fanout": fanout})
    parent_id = q.enqueue(type="up", target_id="12345", output_dir="/tmp/out", limit_n=2)
    asyncio.run(worker.run_once())

    job = q.get(parent_id)
    assert job["status"] == JobStatus.DONE
    assert job["result_path"] is None
    # 子任务已入队
    children = [j for j in q.list() if j["parent_id"] == parent_id]
    assert len(children) == 2

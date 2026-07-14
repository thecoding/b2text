import time
import pytest
from b2text.queue import JobQueue, JobStatus


@pytest.fixture
def q(tmp_path):
    db = tmp_path / "jobs.db"
    queue = JobQueue(db)
    yield queue
    queue.close()


def test_enqueue_creates_job_with_queued_status(q):
    job_id = q.enqueue(type="bv", target_id="BV1xxx", output_dir="/tmp/out")
    job = q.get(job_id)
    assert job is not None
    assert job["id"] == job_id
    assert job["type"] == "bv"
    assert job["target_id"] == "BV1xxx"
    assert job["output_dir"] == "/tmp/out"
    assert job["status"] == JobStatus.QUEUED
    assert job["parent_id"] is None
    assert job["result_path"] is None
    assert job["error"] is None
    assert job["started_at"] is None
    assert job["finished_at"] is None


def test_enqueue_with_parent_id(q):
    parent = q.enqueue(type="up", target_id="12345", output_dir="/tmp/out")
    child = q.enqueue(type="bv", target_id="BV1aaa", output_dir="/tmp/out", parent_id=parent)
    assert q.get(child)["parent_id"] == parent


def test_claim_marks_running(q):
    job_id = q.enqueue(type="bv", target_id="BV1xxx", output_dir="/tmp/out")
    claimed = q.claim_next()
    assert claimed is not None
    assert claimed["id"] == job_id
    assert claimed["status"] == JobStatus.RUNNING
    assert claimed["started_at"] is not None


def test_claim_returns_none_when_empty(q):
    assert q.claim_next() is None


def test_finish_marks_done_with_result_path(q):
    job_id = q.enqueue(type="bv", target_id="BV1xxx", output_dir="/tmp/out")
    q.claim_next()
    q.finish(job_id, result_path="/tmp/out/result.txt")
    job = q.get(job_id)
    assert job["status"] == JobStatus.DONE
    assert job["result_path"] == "/tmp/out/result.txt"
    assert job["finished_at"] is not None


def test_fail_marks_failed_with_error(q):
    job_id = q.enqueue(type="bv", target_id="BV1xxx", output_dir="/tmp/out")
    q.claim_next()
    q.fail(job_id, error="transcribe crashed: NotImplementedError")
    job = q.get(job_id)
    assert job["status"] == JobStatus.FAILED
    assert "transcribe crashed" in job["error"]
    assert job["finished_at"] is not None


def test_cancel_only_works_on_queued(q):
    job_id = q.enqueue(type="bv", target_id="BV1xxx", output_dir="/tmp/out")
    assert q.cancel(job_id) is True
    assert q.get(job_id)["status"] == JobStatus.CANCELLED

    # running 状态不允许取消（防止半成品）
    job2 = q.enqueue(type="bv", target_id="BV1yyy", output_dir="/tmp/out")
    q.claim_next()
    assert q.cancel(job2) is False
    assert q.get(job2)["status"] == JobStatus.RUNNING


def test_list_filters_by_status(q):
    j1 = q.enqueue(type="bv", target_id="BV1a", output_dir="/tmp/out")
    j2 = q.enqueue(type="bv", target_id="BV1b", output_dir="/tmp/out")
    q.claim_next()
    q.finish(j1, result_path="/tmp/out/a.txt")
    rows = q.list(status=JobStatus.DONE)
    assert {r["id"] for r in rows} == {j1}
    rows = q.list(status=JobStatus.QUEUED)
    assert {r["id"] for r in rows} == {j2}


def test_recover_orphans_resets_running_to_queued(q):
    """daemon 被 kill 后，status=running 的孤儿任务应重置为 queued。"""
    job_id = q.enqueue(type="bv", target_id="BV1xxx", output_dir="/tmp/out")
    q.claim_next()
    assert q.get(job_id)["status"] == JobStatus.RUNNING

    recovered = q.recover_orphans()
    assert recovered == 1
    assert q.get(job_id)["status"] == JobStatus.QUEUED

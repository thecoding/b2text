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


def test_count_returns_exact_number(q):
    """count() 返回精确总数，不受 limit 影响。"""
    # 入队 3 条
    id_a = q.enqueue(type="bv", target_id="BV1a", output_dir="/tmp/a")
    id_b = q.enqueue(type="bv", target_id="BV1b", output_dir="/tmp/b")
    id_c = q.enqueue(type="bv", target_id="BV1c", output_dir="/tmp/c")
    assert q.count() == 3
    assert q.count(status=JobStatus.QUEUED) == 3

    # 完成一条
    q.claim_next()
    q.finish(id_a)
    assert q.count() == 3
    assert q.count(status=JobStatus.QUEUED) == 2
    assert q.count(status=JobStatus.DONE) == 1

    # 失败一条
    q.claim_next()
    q.fail(id_b, error="err")
    assert q.count(status=JobStatus.FAILED) == 1

    # 取消一条
    q.cancel(id_c)
    assert q.count(status=JobStatus.CANCELLED) == 1


def test_increment_retry_count(q):
    job_id = q.enqueue(type="bv", target_id="BV1xxx", output_dir="/tmp/out")
    q.increment_retry(job_id)
    q.increment_retry(job_id)
    assert q.get(job_id)["retry_count"] == 2


def test_requeue_resets_status_from_running(q):
    """服务调用 get_video_info 多次失败后重置为 queued 由 worker 再 claim。"""
    job_id = q.enqueue(type="bv", target_id="BV1xxx", output_dir="/tmp/out")
    q.claim_next()
    q.increment_retry(job_id)
    q.requeue(job_id)
    job = q.get(job_id)
    assert job["status"] == JobStatus.QUEUED
    assert job["started_at"] is None
    assert job["retry_count"] == 1


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------


def test_cleanup_by_status_deletes_only_matching(q):
    """cleanup(status=failed) 只删 failed 行，不影响其他状态。"""
    id_a = q.enqueue(type="bv", target_id="BV1a", output_dir="/tmp/a")
    id_b = q.enqueue(type="bv", target_id="BV1b", output_dir="/tmp/b")
    q.claim_next()
    q.fail(id_a, error="boom")
    assert q.count() == 2

    deleted = q.cleanup(status=JobStatus.FAILED)
    assert deleted == 1
    assert q.get(id_a) is None
    assert q.get(id_b) is not None


def test_cleanup_by_age_deletes_only_old(q):
    """cleanup(older_than_seconds=N) 只删 finished_at 早于 N 秒前的。"""
    id_a = q.enqueue(type="bv", target_id="BV1a", output_dir="/tmp/a")
    id_b = q.enqueue(type="bv", target_id="BV1b", output_dir="/tmp/b")
    q.claim_next()
    q.finish(id_a)
    # id_a 刚 finish；id_b 还在 queued（finished_at=NULL）— 不应被时间过滤误删
    time.sleep(0.05)
    deleted = q.cleanup(older_than_seconds=0.01)
    assert deleted == 1
    assert q.get(id_a) is None
    assert q.get(id_b) is not None


def test_cleanup_all_deletes_everything(q):
    id_a = q.enqueue(type="bv", target_id="BV1a", output_dir="/tmp/a")
    id_b = q.enqueue(type="bv", target_id="BV1b", output_dir="/tmp/b")
    q.claim_next()
    q.finish(id_a)
    assert q.count() == 2
    deleted = q.cleanup(all=True)
    assert deleted == 2
    assert q.count() == 0


def test_cleanup_cascades_to_children_of_up_jobs(q):
    """删一个 up 任务时，其 bv 子任务（parent_id 指向它）也要被删除。"""
    up_id = q.enqueue(type="up", target_id="486325909", output_dir="/tmp/o")
    q.claim_next()
    q.finish(up_id)  # up 任务通常立即 done

    # 子任务由 fanout 创建
    child_a = q.enqueue(type="bv", target_id="BV1aaa", output_dir="/tmp/o", parent_id=up_id)
    child_b = q.enqueue(type="bv", target_id="BV1bbb", output_dir="/tmp/o", parent_id=up_id)
    # 另一个无关 bv 任务，不应被删
    other = q.enqueue(type="bv", target_id="BV1xxx", output_dir="/tmp/o")

    deleted = q.cleanup(status=JobStatus.DONE, cascade=True)
    # up + 2 个 child 被删，other 保留
    assert deleted == 3
    assert q.get(up_id) is None
    assert q.get(child_a) is None
    assert q.get(child_b) is None
    assert q.get(other) is not None


def test_cleanup_without_cascade_keeps_orphans(q):
    """cascade=False 时，只删直接匹配的，不动子任务。"""
    up_id = q.enqueue(type="up", target_id="486325909", output_dir="/tmp/o")
    q.claim_next()
    q.finish(up_id)
    child = q.enqueue(type="bv", target_id="BV1aaa", output_dir="/tmp/o", parent_id=up_id)

    deleted = q.cleanup(status=JobStatus.DONE, cascade=False)
    assert deleted == 1
    assert q.get(up_id) is None
    assert q.get(child) is not None  # 留下孤儿


def test_cleanup_no_args_returns_zero(q):
    """不传任何过滤条件 = 啥也不删（避免误删）。"""
    id_a = q.enqueue(type="bv", target_id="BV1a", output_dir="/tmp/a")
    assert q.cleanup() == 0
    assert q.get(id_a) is not None


def test_cleanup_also_deletes_job_logs(q):
    """删 job 的同时清掉对应 job_logs 行（不然 DB 越积越多）。"""
    id_a = q.enqueue(type="bv", target_id="BV1a", output_dir="/tmp/a")
    q.append_log(id_a, '{"msg": "hi"}')
    q.append_log(id_a, '{"msg": "bye"}')
    assert len(q.get_logs(id_a)) == 2

    q.claim_next()
    q.fail(id_a, error="boom")
    deleted = q.cleanup(status=JobStatus.FAILED)
    assert deleted == 1
    assert q.get_logs(id_a) == []

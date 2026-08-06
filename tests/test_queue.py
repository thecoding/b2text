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
    assert job["skip_existing"] == 0  # 默认 False


def test_enqueue_skip_existing_true_round_trips(q):
    job_id = q.enqueue(
        type="up", target_id="12345", output_dir="/tmp/out",
        skip_existing=True,
    )
    assert q.get(job_id)["skip_existing"] == 1


def test_skip_existing_column_present_after_init(q):
    """新库应有 skip_existing 列；旧库构造后也能 ALTER 加上。"""
    job_id = q.enqueue(type="up", target_id="1", output_dir="/tmp")
    cols = {row[1] for row in q._conn.execute("PRAGMA table_info(jobs)").fetchall()}
    assert "skip_existing" in cols


def test_old_schema_with_added_column_round_trips(tmp_path):
    """回归：模拟老 DB（ALTER TABLE 把 skip_existing 加在末尾）→ 新代码 INSERT/读取不出错。

    列序：id, type, target_id, output_dir, limit_n, status, parent_id, result_path,
          error, created_at, started_at, finished_at, retry_count, skip_existing
    之前的 bug：INSERT 用 VALUES (?,?,...) 假设 skip_existing 在第 6 位，结果
    created_at 落到 retry_count 上 → NOT NULL constraint failed。
    """
    import sqlite3
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db), isolation_level=None, check_same_thread=False)
    # 故意建老 schema（13 列，没有 skip_existing）
    conn.executescript("""
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY, type TEXT NOT NULL, target_id TEXT NOT NULL,
            output_dir TEXT NOT NULL, limit_n INTEGER, status TEXT NOT NULL,
            parent_id TEXT, result_path TEXT, error TEXT,
            created_at REAL NOT NULL, started_at REAL, finished_at REAL,
            retry_count INTEGER DEFAULT 0
        );
    """)
    conn.close()

    queue = JobQueue(db)
    try:
        # enqueue 必须成功，且 created_at 不能为 NULL
        job_id = queue.enqueue(
            type="up", target_id="12345", output_dir="/tmp/out",
            limit_n=10, skip_existing=True, parent_id="parent-1",
        )
        job = queue.get(job_id)
        assert job is not None
        assert job["id"] == job_id
        assert job["type"] == "up"
        assert job["target_id"] == "12345"
        assert job["limit_n"] == 10
        assert job["skip_existing"] == 1   # 不再依赖列序
        assert job["status"] == JobStatus.QUEUED
        assert job["parent_id"] == "parent-1"
        assert job["created_at"] is not None
        assert job["finished_at"] is None
    finally:
        queue.close()


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


def test_list_filters_by_multiple_statuses(q):
    """statuses=[QUEUED, RUNNING] 应返回两种状态的并集。"""
    j1 = q.enqueue(type="bv", target_id="BV1a", output_dir="/tmp/out")
    j2 = q.enqueue(type="bv", target_id="BV1b", output_dir="/tmp/out")
    j3 = q.enqueue(type="bv", target_id="BV1c", output_dir="/tmp/out")
    j4 = q.enqueue(type="bv", target_id="BV1d", output_dir="/tmp/out")
    q.claim_next()        # j1 → running
    q.finish(j2, result_path="/tmp/out/b.txt")  # j2 → done
    q.fail(j4, error="boom")                     # j4 → failed（j4 还 queued）

    rows = q.list(statuses=[JobStatus.QUEUED, JobStatus.RUNNING])
    assert {r["id"] for r in rows} == {j1, j3}

    rows = q.list(statuses=[JobStatus.DONE, JobStatus.FAILED])
    assert {r["id"] for r in rows} == {j2, j4}


def test_list_statuses_in_descending_order(q):
    """多状态过滤仍然按 created_at DESC 排序。"""
    import time
    ids = []
    for i in range(3):
        ids.append(q.enqueue(type="bv", target_id=f"BV{i}", output_dir="/tmp"))
        time.sleep(0.01)
    rows = q.list(statuses=[JobStatus.QUEUED])
    assert [r["id"] for r in rows] == list(reversed(ids))


def test_count_filters_by_multiple_statuses(q):
    j1 = q.enqueue(type="bv", target_id="BV1a", output_dir="/tmp/out")
    j2 = q.enqueue(type="bv", target_id="BV1b", output_dir="/tmp/out")
    j3 = q.enqueue(type="bv", target_id="BV1c", output_dir="/tmp/out")
    q.claim_next()  # j1 → running
    q.finish(j2, result_path="/tmp/out/b.txt")
    assert q.count(statuses=[JobStatus.QUEUED, JobStatus.RUNNING]) == 2
    assert q.count(statuses=[JobStatus.QUEUED, JobStatus.DONE]) == 2
    assert q.count(statuses=[JobStatus.DONE]) == 1
    # 不传 statuses → 旧行为不变
    assert q.count() == 3


def test_save_and_get_segments_round_trip(q):
    job_id = q.enqueue(type="bv", target_id="BV1seg", output_dir="/tmp/out")
    q.save_segments(job_id, [
        {"start": 0.0, "end": 1.0, "speaker": "Speaker_1", "text": "你好"},
        {"start": 1.0, "end": 2.5, "speaker": "Speaker_2", "text": "世界"},
    ])
    assert q.get_segments(job_id) == [
        {"start": 0.0, "end": 1.0, "speaker": "Speaker_1", "text": "你好"},
        {"start": 1.0, "end": 2.5, "speaker": "Speaker_2", "text": "世界"},
    ]


def test_save_segments_overwrites_previous(q):
    job_id = q.enqueue(type="bv", target_id="BV1seg", output_dir="/tmp/out")
    q.save_segments(job_id, [{"start": 0.0, "end": 1.0, "speaker": "S1", "text": "旧"}])
    q.save_segments(job_id, [{"start": 5.0, "end": 6.0, "speaker": "S1", "text": "新"}])
    segs = q.get_segments(job_id)
    assert len(segs) == 1
    assert segs[0]["text"] == "新"


def test_cleanup_removes_segments(q):
    job_id = q.enqueue(type="bv", target_id="BV1seg", output_dir="/tmp/out")
    q.fail(job_id, error="boom")
    q.save_segments(job_id, [{"start": 0.0, "end": 1.0, "speaker": "S1", "text": "x"}])
    q.cleanup(status=JobStatus.FAILED)
    assert q.get(job_id) is None
    assert q.get_segments(job_id) == []


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

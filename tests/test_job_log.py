import json
import time
from pathlib import Path
import pytest
from b2text.job_log import JobLog, StepLogger
from b2text.queue import JobQueue


@pytest.fixture
def log_path(tmp_path):
    return tmp_path / "jobs.log"


def test_emit_single_step_start_and_ok(log_path):
    j = JobLog(log_path, job_id="j1")
    j.step_start("get_video_info", bvid="BV1xxx")
    j.step_ok("get_video_info", aid=12345)
    lines = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert len(lines) == 2
    assert lines[0]["step"] == "get_video_info"
    assert lines[0]["msg"] == "start"
    assert lines[0]["extra"]["bvid"] == "BV1xxx"
    assert lines[1]["msg"] == "ok"
    assert lines[1]["extra"]["aid"] == 12345
    for ln in lines:
        assert ln["job_id"] == "j1"
        assert ln["level"] == "INFO"
        assert "ts" in ln


def test_step_fail_includes_stacktrace(log_path):
    j = JobLog(log_path, job_id="j2")
    try:
        raise ValueError("boom")
    except ValueError:
        j.step_fail("transcribe", exc_info=True, extra={"chunk_index": 0})
    lines = [json.loads(line) for line in log_path.read_text().splitlines()]
    err = next(ln for ln in lines if ln["level"] == "ERROR")
    assert "boom" in err["extra"]["exc_message"]
    assert err["extra"]["chunk_index"] == 0
    assert "ValueError" in err["extra"]["exc_type"]
    assert "traceback" in err["extra"]["stacktrace"].lower()


def test_step_logger_context_manager(log_path):
    j = JobLog(log_path, job_id="j3")
    with j.step("convert_wav") as s:
        s.set(size_bytes=12345)
    lines = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert [ln["msg"] for ln in lines] == ["start", "ok"]
    assert lines[1]["extra"]["size_bytes"] == 12345


def test_step_logger_records_fail_on_exception(log_path):
    j = JobLog(log_path, job_id="j4")
    with pytest.raises(RuntimeError):
        with j.step("chunk_audio"):
            raise RuntimeError("ffmpeg exit 1")
    lines = [json.loads(line) for line in log_path.read_text().splitlines()]
    msg_seq = [ln["msg"] for ln in lines]
    assert msg_seq == ["start", "fail"]
    assert lines[-1]["level"] == "ERROR"


def test_ts_is_iso8601(log_path):
    j = JobLog(log_path, job_id="j5")
    j.info("boot")
    line = json.loads(log_path.read_text().strip())
    # ISO 8601 with timezone offset (or Z)
    assert "T" in line["ts"]
    assert line["ts"].endswith("Z") or "+" in line["ts"] or line["ts"].count(":") == 2


def test_writes_use_utf8_for_chinese(log_path):
    j = JobLog(log_path, job_id="j6")
    j.info("开始处理", extra={"title": "面试录音"})
    line = json.loads(log_path.read_text().strip(), )
    assert line["extra"]["title"] == "面试录音"


def test_writes_to_queue_when_provided(tmp_path):
    """传入 queue 后，log 同时写入 SQLite job_logs 表。"""
    db_path = tmp_path / "jobs.db"
    log_path = tmp_path / "jobs.log"
    q = JobQueue(db_path)
    try:
        j = JobLog(log_path, job_id="j7", queue=q)
        j.info("hello from queue", extra={"val": 42})

        # 验证 DB 中有记录
        lines = q.get_logs("j7")
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["msg"] == "hello from queue"
        assert parsed["extra"]["val"] == 42
        assert parsed["job_id"] == "j7"

        # 验证文件也有记录
        file_lines = log_path.read_text().splitlines()
        assert len(file_lines) == 1
        assert json.loads(file_lines[0])["msg"] == "hello from queue"
    finally:
        q.close()


def test_queue_writes_pending_and_set(tmp_path):
    """set() 暂存的字段在 step_start 时合并，写入 DB。"""
    db_path = tmp_path / "jobs.db"
    log_path = tmp_path / "jobs.log"
    q = JobQueue(db_path)
    try:
        j = JobLog(log_path, job_id="j8", queue=q)
        j.set(bvid="BV1xxx", limit=10)
        j.step_start("fetch")
        j.step_ok("fetch", count=5)

        lines = q.get_logs("j8")
        assert len(lines) == 2
        start_line = json.loads(lines[0])
        ok_line = json.loads(lines[1])
        assert start_line["msg"] == "start"
        assert start_line["extra"]["bvid"] == "BV1xxx"
        assert start_line["extra"]["limit"] == 10
        assert ok_line["msg"] == "ok"
        assert ok_line["extra"]["count"] == 5
    finally:
        q.close()

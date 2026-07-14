import json
import time
from pathlib import Path
import pytest
from b2text.job_log import JobLog, StepLogger


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

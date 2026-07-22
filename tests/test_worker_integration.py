import asyncio
import json
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from b2text.queue import JobQueue, JobStatus
from b2text.worker import build_default_steps


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    db = tmp_path / "jobs.db"
    log = tmp_path / "jobs.log"
    q = JobQueue(db)
    yield q, log
    q.close()


def _mock_video_info(bvid):
    return {
        "bvid": bvid,
        "aid": 12345,
        "title": "测试视频",
        "owner": "tester",
        "pages": [{"cid": 999, "title": "P1", "page": 1}],
        "videos": 1,
        "ugc_season": None,
    }


def test_full_pipeline_writes_txt(env):
    """Mock 所有外部副作用，跑完整 pipeline 7 步。"""
    q, log_path = env
    output_dir = Path(env[0].db_path).parent / "out"
    output_dir.mkdir()

    transcriber = MagicMock()
    transcriber.transcribe.return_value = []
    steps = build_default_steps(cookie="SESSDATA=t", transcriber=transcriber, queue=q)

    def fake_dl(url, out, cookie):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"fake audio")
        return out

    def fake_extract(mp4_path, wav_path):
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        wav_path.write_bytes(b"fake wav")
        return wav_path

    async def run():
        with patch("b2text.bili_api.get_video_info", return_value=_mock_video_info("BV1xxx")), \
             patch("b2text.bili_api.get_audio_url", return_value="https://example.com/audio.m4s"), \
             patch("b2text.audio.download_audio_stream", side_effect=fake_dl), \
             patch("b2text.audio.extract_audio_from_mp4", side_effect=fake_extract):
            from b2text.worker import Worker
            worker = Worker(queue=q, log_path=log_path, cookie="SESSDATA=t", steps=steps)
            job_id = q.enqueue(type="bv", target_id="BV1xxx", output_dir=str(output_dir))
            await worker.run_once()
            return q.get(job_id)

    job = asyncio.run(run())
    assert job["status"] == JobStatus.DONE
    assert job["result_path"].endswith(".txt")


def test_bili_api_retries_3_times_then_fails(env, monkeypatch):
    """B 站 API 失败 3 次后标记 failed，retry_count=3。"""
    from b2text.worker import Worker
    q, log_path = env
    monkeypatch.setattr("b2text.worker.time.sleep", lambda _: None)

    call_count = {"n": 0}

    def always_fail(bvid, **kwargs):
        call_count["n"] += 1
        raise ConnectionError("network down")

    transcriber = MagicMock()
    transcriber.transcribe.return_value = []
    steps = build_default_steps(cookie="dummy", transcriber=transcriber, queue=q)

    with patch("b2text.bili_api.get_video_info", side_effect=always_fail):
        worker = Worker(queue=q, log_path=log_path, cookie="dummy", steps=steps)
        job_id = q.enqueue(type="bv", target_id="BV1xxx", output_dir="/tmp/out")
        asyncio.run(worker.run_once())

    job = q.get(job_id)
    assert job["status"] == JobStatus.FAILED
    assert call_count["n"] == 3
    assert job["retry_count"] == 3


def test_bili_api_succeeds_on_third_attempt(env, monkeypatch):
    """B 站 API 失败 2 次后第 3 次成功 → retry_count=2、status=done。"""
    from b2text.worker import Worker
    q, log_path = env
    monkeypatch.setattr("b2text.worker.time.sleep", lambda _: None)

    attempts = {"n": 0}

    def flaky(bvid, **kwargs):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ConnectionError(f"attempt {attempts['n']} failed")
        return _mock_video_info(bvid)

    transcriber = MagicMock()
    transcriber.transcribe.return_value = []

    steps = build_default_steps(cookie="SESSDATA=t", transcriber=transcriber, queue=q)

    def fake_dl(url, out, cookie):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"x")
        return out

    def fake_extract(mp4_path, wav_path):
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        wav_path.write_bytes(b"x")
        return wav_path

    with patch("b2text.bili_api.get_video_info", side_effect=flaky), \
         patch("b2text.bili_api.get_audio_url", return_value="https://example.com/audio.m4s"), \
         patch("b2text.audio.download_audio_stream", side_effect=fake_dl), \
         patch("b2text.audio.extract_audio_from_mp4", side_effect=fake_extract):
        from b2text.worker import Worker
        worker = Worker(queue=q, log_path=log_path, cookie="SESSDATA=t", steps=steps)
        output_dir = Path(env[0].db_path).parent / "out"
        output_dir.mkdir()
        job_id = q.enqueue(type="bv", target_id="BV1xxx", output_dir=str(output_dir))
        asyncio.run(worker.run_once())

    job = q.get(job_id)
    assert job["status"] == JobStatus.DONE
    assert job["retry_count"] == 2


def test_fanout_retries_on_upmaster_api_error(env, monkeypatch):
    """fanout 失败 (B 站 code=-799) 应触发 _with_api_retry 重试。

    不重试的话一次 -799 就杀死整个 up 任务，用户得手动重提交。
    """
    from b2text.worker import Worker
    from b2text.upmaster import UpmasterAPIError

    q, log_path = env
    monkeypatch.setattr("b2text.worker.time.sleep", lambda _: None)

    attempts = {"n": 0}

    def flaky(uid, limit, *, cookie):
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise UpmasterAPIError(
                code=-799, message="请求过于频繁，请稍后再试", uid=uid,
            )
        return ["BV1aaa", "BV1bbb"]

    transcriber = MagicMock()
    transcriber.transcribe.return_value = []
    steps = build_default_steps(cookie="dummy", transcriber=transcriber, queue=q)

    with patch("b2text.upmaster.fetch_up_videos", side_effect=flaky):
        worker = Worker(queue=q, log_path=log_path, cookie="dummy", steps=steps)
        job_id = q.enqueue(type="up", target_id="486325909",
                           output_dir="/tmp/out", limit_n=2)
        asyncio.run(worker.run_once())

    job = q.get(job_id)
    assert job["status"] == JobStatus.DONE
    assert attempts["n"] == 2
    assert job["retry_count"] == 1
    # 子任务也已入队
    children = q.list()
    assert any(c.get("parent_id") == job_id for c in children)

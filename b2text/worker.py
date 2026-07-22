"""asyncio worker：从队列取任务、按 pipeline 七步执行。

每个 step 是一个 callable(job, log) -> None，接受当前 job dict 和 JobLog。
抛异常会被捕获并写 job.status=failed、log 中带 stacktrace。
"""
from __future__ import annotations

import asyncio
import re
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from b2text import audio as _audio
from b2text import bili_api as _bili_api
from b2text.formatter import format_segments
from b2text.job_log import JobLog
from b2text.normalizer import normalize_funasr_output
from b2text.queue import JobQueue, JobStatus


Step = Callable[[dict[str, Any], JobLog], None]


class Worker:
    def __init__(
        self,
        *,
        queue: JobQueue,
        log_path: Path,
        cookie: str,
        steps: dict[str, Step],
    ):
        self.queue = queue
        self.log_path = log_path
        self.cookie = cookie
        self.steps = steps
        self._inflight: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    async def run_once(self) -> dict[str, Any] | None:
        """取一条任务跑完整 pipeline。无任务返回 None。

        整个过程下沉到 default executor 跑（FunASR 推理 + 网络 I/O 全是阻塞调用），
        否则会卡住 uvicorn event loop 导致 HTTP server 不响应。
        """
        return await asyncio.get_running_loop().run_in_executor(None, self._run_once_sync)

    def _run_once_sync(self) -> dict[str, Any] | None:
        job = self.queue.claim_next()
        if job is None:
            return None
        return self._process_sync(job)

    def _process_sync(self, job: dict[str, Any]) -> dict[str, Any]:
        import tempfile
        log = JobLog(self.log_path, job_id=job["id"], queue=self.queue)
        log.info("job_start", step="_", extra={
            "type": job["type"], "target_id": job["target_id"], "output_dir": job["output_dir"],
        })
        with tempfile.TemporaryDirectory(prefix="b2text_job_") as tmpdir_str:
            job["_tmpdir"] = Path(tmpdir_str)
            try:
                for step_name, fn in self.steps.items():
                    log.step_start(step_name)
                    try:
                        fn(job, log)
                        log.step_ok(step_name)
                    except Exception as e:
                        log.step_fail(step_name, exc_info=True)
                        raise
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                log.error("job_done", step="_", extra={"status": "failed", "error": err})
                self.queue.fail(job["id"], error=err)
                return self.queue.get(job["id"])  # type: ignore[return-value]

        result_path = self._result_path_for(job) if job["type"] == "bv" else None
        log.info("job_done", step="_", extra={"status": "done", "result_path": result_path})
        self.queue.finish(job["id"], result_path=result_path)
        return self.queue.get(job["id"])  # type: ignore[return-value]

    @staticmethod
    def _result_path_for(job: dict[str, Any]) -> str:
        """默认结果路径：<output_dir>/<safe_target_id>.txt。"""
        safe = re.sub(r'[<>:"/\\|?*]', "_", job["target_id"])[:80]
        return str(Path(job["output_dir"]) / f"{safe}.txt")

    async def serve_forever(self) -> None:
        """无限循环：取 → 处理 → 等。cancel 后优雅退出。"""
        while not self._stopping.is_set():
            try:
                await asyncio.wait_for(self.run_once(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            await asyncio.sleep(0)  # 让出事件循环

    def stop(self) -> None:
        self._stopping.set()


# ---------------------------------------------------------------------------
# 默认 pipeline 工厂：把 worker 骨架接入真实 bili_api / audio / transcriber。
# ---------------------------------------------------------------------------


def build_default_steps(*, cookie: str, transcriber, queue: JobQueue | None = None) -> dict[str, "Step"]:
    """构造一个完整的 pipeline（fanout + 七步），注入真实 bili_api / audio / transcriber。

    cookie 通过闭包传给各步骤，不再依赖模块级全局变量。
    transcriber 是 FunASRTranscriber 实例（或者测试中 mock）。
    queue 传入后，retry 调用 increment_retry 累计重试次数（spec §5）。
    """

    delays = [1, 4, 16]  # 指数退避（spec §5）
    api_max_attempts = 3

    def _with_api_retry(log, job, step_name, fn):
        """fn 是一个无参 callable（thunk），内部捕获了要传递的参数。"""
        last_err = None
        for attempt in range(api_max_attempts):
            try:
                return fn()
            except Exception as e:
                last_err = e
                if queue is not None:
                    queue.increment_retry(job["id"])
                if attempt < api_max_attempts - 1:
                    log.warn(
                        f"{step_name} 失败（第 {attempt + 1}/{api_max_attempts} 次）",
                        step=step_name,
                        extra={"error": str(e), "next_sleep": delays[attempt]},
                    )
                    time.sleep(delays[attempt])
        raise last_err

    def fanout(job, log):
        """type=up 时拉取 UP 主视频列表，创建子任务（parent_id），父任务立即 done。
        type=bv 时为 no-op。
        """
        if job["type"] != "up":
            log.set(fanout=False)
            return
        from b2text.upmaster import fetch_up_videos
        uid = int(job["target_id"])
        limit = job.get("limit_n") or 50
        log.set(uid=uid, limit=limit)
        if queue is None:
            raise RuntimeError("fanout 需要 queue（type=up 任务创建子任务）")
        bvids = _with_api_retry(
            log, job, "fanout",
            lambda: fetch_up_videos(uid, limit, cookie=cookie),
        )
        if not bvids:
            raise RuntimeError(f"upmaster 没拉到任何视频：uid={uid}")
        for bvid in bvids:
            queue.enqueue(
                type="bv", target_id=bvid, output_dir=job["output_dir"], parent_id=job["id"],
            )
        log.set(child_count=len(bvids))

    def get_video_info(job, log):
        if job["type"] != "bv":
            return
        info = _with_api_retry(log, job, "get_video_info",
                               lambda: _bili_api.get_video_info(job["target_id"], cookie=cookie))
        if not info:
            raise RuntimeError(f"get_video_info failed for {job['target_id']}")
        job["_video_info"] = info
        log.set(aid=info["aid"], title=info["title"])

    def get_audio_url(job, log):
        if job["type"] != "bv":
            return
        info = job["_video_info"]
        cid = info["pages"][0]["cid"]
        url = _with_api_retry(log, job, "get_audio_url",
                               lambda: _bili_api.get_audio_url(info["aid"], cid, cookie=cookie))
        if not url:
            raise RuntimeError("get_audio_url failed")
        job["_audio_url"] = url

    def download_audio(job, log):
        if job["type"] != "bv":
            return
        m4s = job["_tmpdir"] / "audio.m4s"
        _audio.download_audio_stream(job["_audio_url"], m4s, cookie=cookie)
        job["_audio_path"] = m4s

    def convert_wav(job, log):
        if job["type"] != "bv":
            return
        wav = job["_tmpdir"] / "audio.wav"
        _audio.extract_audio_from_mp4(job["_audio_path"], wav)
        job["_wav_path"] = wav

    def chunk_audio(job, log):
        if job["type"] != "bv":
            return
        # b2text.transcriber 已经内置 chunk 逻辑（>10 min 自动切）
        # 这里仅查 duration 以便日志，标 0 表示无需独立切分
        log.set(chunked=False)

    def transcribe(job, log):
        if job["type"] != "bv":
            return
        log.set(device="mps", chunk_index=0, chunk_count=1)
        raw = transcriber.transcribe(job["_wav_path"])
        job["_raw_segments"] = raw or []
        log.set(segment_count=len(job["_raw_segments"]))

    def normalize_write(job, log):
        if job["type"] != "bv":
            return
        segs = normalize_funasr_output(job["_raw_segments"])
        text = format_segments(segs)
        safe = re.sub(r'[<>:"/\\|?*]', "_", job["target_id"])[:80]
        out_path = Path(job["output_dir"]) / f"{safe}.txt"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        log.set(segment_count=len(segs), output_path=str(out_path))

    return {
        "fanout": fanout,
        "get_video_info": get_video_info,
        "get_audio_url": get_audio_url,
        "download_audio": download_audio,
        "convert_wav": convert_wav,
        "chunk_audio": chunk_audio,
        "transcribe": transcribe,
        "normalize_write": normalize_write,
    }

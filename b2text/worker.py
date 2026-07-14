"""asyncio worker：从队列取任务、按 pipeline 七步执行。

每个 step 是一个 callable(job, log) -> None，接受当前 job dict 和 JobLog。
抛异常会被捕获并写 job.status=failed、log 中带 stacktrace。
"""
from __future__ import annotations

import asyncio
import re
import traceback
from pathlib import Path
from typing import Any, Awaitable, Callable

from b2text.job_log import JobLog
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
        """取一条任务跑完整 pipeline。无任务返回 None。"""
        job = self.queue.claim_next()
        if job is None:
            return None
        return await self._process(job)

    async def _process(self, job: dict[str, Any]) -> dict[str, Any]:
        log = JobLog(self.log_path, job_id=job["id"])
        log.info("job_start", step="_", extra={
            "type": job["type"], "target_id": job["target_id"], "output_dir": job["output_dir"],
        })
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

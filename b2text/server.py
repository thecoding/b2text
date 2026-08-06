"""HTTP daemon for b2text."""
from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from b2text.paths import data_dir
from b2text.queue import JobQueue, JobStatus


@dataclass
class AppContext:
    db_path: Path
    log_path: Path
    cookie: str
    run_real_pipeline: bool


class TranscribeRequest(BaseModel):
    type: str = Field(..., pattern="^(bv|up)$")
    id: str = Field(..., min_length=1)
    output_dir: str | None = Field(None, min_length=1,
                                   description="输出目录；不传时默认 data_dir/extension（扩展场景）")
    limit: int | None = Field(None, ge=1, description="UP 任务最多拉取的视频数；超过 50 时自动翻页")
    skip_existing: bool = Field(False, description="UP 任务跳过 output_dir 下已存在的 .txt")


_BVID_RE = re.compile(r"^BV[a-zA-Z0-9]+$")


def _validate_bvid(value: str) -> bool:
    return bool(_BVID_RE.match(value))


def _parse_log_line(raw: str) -> dict[str, Any] | None:
    """解析单行 JSONL，返回 dict 或 None。"""
    raw = raw.strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


def _read_log_lines_from_db(queue: JobQueue, job_id: str) -> list[dict[str, Any]]:
    """从 SQLite job_logs 表读取指定 job 的日志行。"""
    raw_lines = queue.get_logs(job_id)
    entries: list[dict[str, Any]] = []
    for raw in raw_lines:
        rec = _parse_log_line(raw)
        if rec is not None:
            entries.append(rec)
    return entries


def _current_steps_batch(log_path: Path, job_ids: list[str]) -> dict[str, dict[str, Any] | None]:
    """单次扫 log_path，返回每个 job_id 的最后一条 step 记录。

    每条 job 在 list 里只关心"最后一个 step 是啥 / 状态是 start/ok/fail"，
    所以扫一次完整文件按 job_id keep-last 即可。log 文件通常几 MB，O(N) 可接受。
    """
    wanted = set(job_ids)
    latest: dict[str, dict[str, Any]] = {}
    if not log_path.exists():
        return {jid: None for jid in job_ids}
    with log_path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            jid = rec.get("job_id")
            if jid in wanted:
                latest[jid] = {"step": rec.get("step", "_"), "msg": rec.get("msg", "")}
    return {jid: latest.get(jid) for jid in job_ids}


def build_app(ctx: AppContext) -> FastAPI:
    """Construct the FastAPI app with per-request queue dependencies.

    The JobQueue wraps a SQLite connection that is bound to the thread that
    created it (sqlite3 default). FastAPI runs sync endpoint bodies in a
    threadpool, so we cannot share a single connection across requests.
    Instead we open a fresh JobQueue per request via the get_queue dependency
    below — the connection is bound to that handler's thread and closed
    automatically when the request scope ends.
    """
    def get_queue():
        """FastAPI dependency: yield a JobQueue and close it when the request ends."""
        q = JobQueue(ctx.db_path)
        try:
            yield q
        finally:
            q.close()

    state: dict[str, Any] = {
        "ctx": ctx,
        "worker": None,
        "model_loaded": False,
        "model_error": None,
    }

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # A single shared queue for orphan recovery and worker.
        shared_queue = JobQueue(ctx.db_path)

        # Recover orphans synchronously in this (lifespan) thread.
        recovered = shared_queue.recover_orphans()
        if recovered:
            print(f"[server] recovered {recovered} orphan tasks", flush=True)

        if ctx.run_real_pipeline:
            from b2text.worker import Worker, build_default_steps
            from b2text.transcriber import FunASRTranscriber
            transcriber = FunASRTranscriber()
            steps = build_default_steps(cookie=ctx.cookie, transcriber=transcriber,
                                        queue=shared_queue)

            # Load model synchronously (in executor) before starting worker,
            # to avoid race where worker tries to transcribe before model is ready.
            try:
                await asyncio.get_running_loop().run_in_executor(
                    None, transcriber._load_model
                )
                state["model_loaded"] = True
                print("[server] FunASR model loaded", flush=True)
            except Exception as exc:
                state["model_error"] = str(exc)
                print(f"[server] model load failed: {exc!r}", flush=True)

            if state["model_loaded"]:
                worker = Worker(queue=shared_queue, log_path=ctx.log_path,
                                cookie=ctx.cookie, steps=steps)
                state["worker"] = asyncio.create_task(worker.serve_forever())
        yield
        if state["worker"]:
            state["worker"].cancel()
            try:
                await state["worker"]
            except asyncio.CancelledError:
                pass
        shared_queue.close()

    app = FastAPI(title="b2text daemon", lifespan=lifespan)
    app.state.ctx = ctx
    # 本地 daemon 同时服务 Chrome 扩展等本地客户端；扩展 fetch 带 chrome-extension:// Origin，
    # 用通配来源放行（只绑定 127.0.0.1，风险可控）。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health(queue: JobQueue = Depends(get_queue)):
        model_loaded = state.get("model_loaded", False) if ctx.run_real_pipeline else True
        body: dict[str, Any] = {
            "ok": model_loaded,
            "model_loaded": model_loaded,
            "queue_len": queue.count(status=JobStatus.QUEUED),
            "running": queue.count(status=JobStatus.RUNNING),
        }
        if state.get("model_error"):
            body["model_error"] = state["model_error"]
        code = 200 if model_loaded else 503
        return JSONResponse(body, status_code=code)

    @app.post("/transcribe")
    def submit(req: TranscribeRequest, queue: JobQueue = Depends(get_queue)):
        if req.type == "bv" and not _validate_bvid(req.id):
            raise HTTPException(400, detail="invalid bv id format")
        if req.type == "up":
            try:
                uid = int(req.id)
            except ValueError:
                raise HTTPException(400, detail="up id must be integer")
            if not (1 <= uid <= 10**11):
                raise HTTPException(400, detail="up id out of range")
        if ctx.run_real_pipeline and not state.get("model_loaded", False):
            detail = {"error": "model_loading"}
            if state.get("model_error"):
                detail["error"] = "model_load_failed"
                detail["message"] = state["model_error"]
            raise HTTPException(503, detail=detail)
        output_dir = req.output_dir or str(data_dir() / "extension")
        job_id = queue.enqueue(
            type=req.type,
            target_id=req.id,
            output_dir=output_dir,
            limit_n=req.limit,
            skip_existing=req.skip_existing,
        )
        return {"task_id": job_id}

    @app.get("/tasks")
    def list_tasks(status: str | None = Query(None), limit: int = 50,
                   offset: int = 0,
                   uncompleted: bool = Query(False),
                   queue: JobQueue = Depends(get_queue)):
        if uncompleted:
            statuses = [JobStatus.QUEUED, JobStatus.RUNNING]
            tasks = queue.list(statuses=statuses, limit=limit, offset=offset)
            total = queue.count(statuses=statuses)
        else:
            st = JobStatus(status) if status else None
            tasks = queue.list(status=st, limit=limit, offset=offset)
            total = queue.count(status=st)
        # 给每条任务附加"最后处理的 step + 状态（start/ok/fail/job_done）"，
        # 让 CLI 表格可以显示进度而不必单独 GET /tasks/{id}/log。
        progress_map = _current_steps_batch(ctx.log_path, [t["id"] for t in tasks])
        for t in tasks:
            t["progress"] = progress_map.get(t["id"])
        return {
            "tasks": tasks,
            "total": total,
        }

    @app.get("/tasks/{task_id}")
    def get_task(task_id: str, queue: JobQueue = Depends(get_queue)):
        job = queue.get(task_id)
        if job is None:
            raise HTTPException(404, detail="not found")
        return job

    @app.get("/tasks/{task_id}/segments")
    def get_task_segments(task_id: str, queue: JobQueue = Depends(get_queue)):
        """返回任务转写时间线；任务未完成时返回 202 + 当前状态。"""
        job = queue.get(task_id)
        if job is None:
            raise HTTPException(404, detail="not found")
        if job["status"] != JobStatus.DONE.value:
            return JSONResponse(
                {"status": job["status"], "segments": [], "duration": 0.0},
                status_code=202,
            )
        segments = queue.get_segments(task_id)
        duration = max((s["end"] for s in segments), default=0.0)
        return {
            "status": job["status"],
            "segments": segments,
            "duration": duration,
        }

    @app.get("/tasks/{task_id}/log")
    def get_task_log(task_id: str, queue: JobQueue = Depends(get_queue)):
        if queue.get(task_id) is None:
            raise HTTPException(404, detail="not found")
        logs = _read_log_lines_from_db(queue, task_id)
        return {"logs": logs}

    @app.delete("/tasks/{task_id}")
    def cancel_task(task_id: str, queue: JobQueue = Depends(get_queue)):
        if queue.get(task_id) is None:
            raise HTTPException(404, detail="not found")
        if queue.cancel(task_id):
            return JSONResponse({"status": "cancelled"}, status_code=200)
        raise HTTPException(409, detail="not cancellable in current status")

    @app.delete("/tasks")
    def bulk_delete_tasks(
        status: str | None = Query(None),
        older_than_seconds: float | None = Query(None, alias="older_than_seconds"),
        all: bool = Query(False, alias="all"),
        cascade: bool = Query(True),
        queue: JobQueue = Depends(get_queue),
    ):
        """批量删除任务。必须传至少一个过滤条件，否则 400。"""
        if status is None and older_than_seconds is None and not all:
            raise HTTPException(400, detail="no filter provided (status/older_than/all)")
        st = JobStatus(status) if status else None
        deleted = queue.cleanup(
            status=st, older_than_seconds=older_than_seconds,
            all=all, cascade=cascade,
        )
        return {"deleted": deleted}

    return app


def main():
    import argparse
    from b2text.cookie_store import resolve_cookie, MissingCookieError
    from b2text.paths import jobs_db, jobs_log

    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-funasr", action="store_true",
                   help="don't load FunASR (for testing)")
    args = p.parse_args()

    try:
        cookie = resolve_cookie()
    except MissingCookieError as e:
        print(f"❌ {e}", flush=True)
        raise SystemExit(4)

    ctx = AppContext(
        db_path=jobs_db(), log_path=jobs_log(),
        cookie=cookie, run_real_pipeline=not args.no_funasr,
    )
    app = build_app(ctx)
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()

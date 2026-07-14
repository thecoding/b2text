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
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

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
    output_dir: str = Field(..., min_length=1)
    limit: int | None = Field(None, ge=1, le=50)


_BVID_RE = re.compile(r"^BV[a-zA-Z0-9]+$")


def _validate_bvid(value: str) -> bool:
    return bool(_BVID_RE.match(value))


def _read_log_lines(log_path: Path, job_id: str) -> list[dict[str, Any]]:
    """Read JSONL log file and return parsed entries whose job_id matches.

    The JobQueue.get_logs reads from the DB table, but the test (and the
    worker's JobLog writer) write to the shared JSONL file. This endpoint
    reads from the file directly so logs reflect what the worker actually
    emits at runtime.
    """
    entries: list[dict[str, Any]] = []
    if not log_path.exists():
        return entries
    with log_path.open("r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                rec = {"raw": raw}
            if isinstance(rec, dict) and rec.get("job_id") == job_id:
                entries.append(rec)
            elif isinstance(rec, dict) and "raw" in rec:
                # Unparseable line: fall back to substring matching on the raw
                # text because there is no parsed `job_id` field to filter by.
                if job_id in raw:
                    entries.append(rec)
    return entries


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
        # A separate queue owned by this lifespan coroutine for orphan recovery
        # and the background worker. Per-request queues come from get_queue().
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

            async def _load_model_then_mark_ready():
                try:
                    await asyncio.get_running_loop().run_in_executor(
                        None, transcriber._load_model
                    )
                except Exception as exc:
                    print(f"[server] model load failed: {exc!r}", flush=True)
                    state["model_error"] = str(exc)
                    return
                state["model_loaded"] = True

            asyncio.create_task(_load_model_then_mark_ready())
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

    @app.get("/health")
    def health(queue: JobQueue = Depends(get_queue)):
        model_loaded = state.get("model_loaded", False) if ctx.run_real_pipeline else True
        body: dict[str, Any] = {
            "ok": model_loaded,
            "model_loaded": model_loaded,
            "queue_len": len(queue.list(status=JobStatus.QUEUED)),
            "running": len(queue.list(status=JobStatus.RUNNING)),
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
            raise HTTPException(503, detail={"error": "model_loading"})
        job_id = queue.enqueue(
            type=req.type,
            target_id=req.id,
            output_dir=req.output_dir,
            limit_n=req.limit,
        )
        return {"task_id": job_id}

    @app.get("/tasks")
    def list_tasks(status: str | None = Query(None), limit: int = 50,
                   offset: int = 0, queue: JobQueue = Depends(get_queue)):
        st = JobStatus(status) if status else None
        return {
            "tasks": queue.list(status=st, limit=limit, offset=offset),
            "total": len(queue.list(status=st)),
        }

    @app.get("/tasks/{task_id}")
    def get_task(task_id: str, queue: JobQueue = Depends(get_queue)):
        job = queue.get(task_id)
        if job is None:
            raise HTTPException(404, detail="not found")
        return job

    @app.get("/tasks/{task_id}/log")
    def get_task_log(task_id: str, queue: JobQueue = Depends(get_queue)):
        if queue.get(task_id) is None:
            raise HTTPException(404, detail="not found")
        logs = _read_log_lines(ctx.log_path, task_id)
        return {"logs": logs}

    @app.delete("/tasks/{task_id}")
    def cancel_task(task_id: str, queue: JobQueue = Depends(get_queue)):
        if queue.get(task_id) is None:
            raise HTTPException(404, detail="not found")
        if queue.cancel(task_id):
            return JSONResponse({"status": "cancelled"}, status_code=200)
        raise HTTPException(409, detail="not cancellable in current status")

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
"""SQLite-backed 任务队列（CRUD + 孤儿恢复 + 日志行持久化）。"""
from __future__ import annotations

import sqlite3
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Any


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    type         TEXT NOT NULL,
    target_id    TEXT NOT NULL,
    output_dir   TEXT NOT NULL,
    limit_n      INTEGER,
    status       TEXT NOT NULL,
    parent_id    TEXT,
    result_path  TEXT,
    error        TEXT,
    created_at   REAL NOT NULL,
    started_at   REAL,
    finished_at  REAL,
    retry_count  INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_status  ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_created ON jobs(created_at);

CREATE TABLE IF NOT EXISTS job_logs (
    job_id  TEXT NOT NULL,
    seq     INTEGER NOT NULL,
    line    TEXT NOT NULL,
    PRIMARY KEY (job_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_job_logs_job ON job_logs(job_id);
"""


class JobQueue:
    """单进程 SQLite job queue。所有方法线程/协程安全（serialized by SQLite）。"""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False：worker 通过 run_in_executor 在独立线程里跑；
        # FastAPI sync endpoints 通过 Depends 起独立连接、也在 threadpool 线程里跑。
        # 配合 WAL 模式，sqlite3 自身加锁保证多线程访问安全。
        self._conn = sqlite3.connect(str(db_path), isolation_level=None, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # ---------- CRUD ----------
    def enqueue(
        self,
        *,
        type: str,
        target_id: str,
        output_dir: str,
        limit_n: int | None = None,
        parent_id: str | None = None,
    ) -> str:
        job_id = str(uuid.uuid4())
        now = time.time()
        self._conn.execute(
            "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (job_id, type, target_id, output_dir, limit_n,
             JobStatus.QUEUED.value, parent_id, None, None,
             now, None, None, 0),
        )
        return job_id

    def claim_next(self) -> dict[str, Any] | None:
        """原子地取一条 queued 任务、置为 running 并返回（带 started_at）。"""
        cur = self._conn.execute(
            "SELECT id FROM jobs WHERE status = ? ORDER BY created_at LIMIT 1",
            (JobStatus.QUEUED.value,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        job_id = row[0]
        now = time.time()
        self._conn.execute(
            "UPDATE jobs SET status=?, started_at=? WHERE id=?",
            (JobStatus.RUNNING.value, now, job_id),
        )
        return self.get(job_id)

    def finish(self, job_id: str, *, result_path: str | None = None) -> None:
        self._conn.execute(
            "UPDATE jobs SET status=?, result_path=?, finished_at=? WHERE id=?",
            (JobStatus.DONE.value, result_path, time.time(), job_id),
        )

    def fail(self, job_id: str, *, error: str) -> None:
        self._conn.execute(
            "UPDATE jobs SET status=?, error=?, finished_at=? WHERE id=?",
            (JobStatus.FAILED.value, error, time.time(), job_id),
        )

    def cancel(self, job_id: str) -> bool:
        """仅对 queued 状态生效。"""
        cur = self._conn.execute(
            "UPDATE jobs SET status=?, finished_at=? WHERE id=? AND status=?",
            (JobStatus.CANCELLED.value, time.time(), job_id, JobStatus.QUEUED.value),
        )
        return cur.rowcount > 0

    def get(self, job_id: str) -> dict[str, Any] | None:
        cur = self._conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,))
        row = cur.fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def list(
        self,
        *,
        status: JobStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        if status is not None:
            cur = self._conn.execute(
                "SELECT * FROM jobs WHERE status=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (status.value, limit, offset),
            )
        else:
            cur = self._conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        return [self._row_to_dict(r) for r in cur.fetchall()]

    # ---------- 恢复 ----------
    def recover_orphans(self) -> int:
        """把 status=running 的任务（daemon crash 残留）重置为 queued，返回数量。"""
        cur = self._conn.execute(
            "UPDATE jobs SET status=?, started_at=NULL WHERE status=?",
            (JobStatus.QUEUED.value, JobStatus.RUNNING.value),
        )
        return cur.rowcount

    # ---------- 日志 ----------
    def append_log(self, job_id: str, line: str) -> None:
        cur = self._conn.execute(
            "SELECT COALESCE(MAX(seq), -1)+1 FROM job_logs WHERE job_id=?",
            (job_id,),
        )
        next_seq = cur.fetchone()[0]
        self._conn.execute(
            "INSERT INTO job_logs (job_id, seq, line) VALUES (?, ?, ?)",
            (job_id, next_seq, line),
        )

    def get_logs(self, job_id: str) -> list[str]:
        cur = self._conn.execute(
            "SELECT line FROM job_logs WHERE job_id=? ORDER BY seq",
            (job_id,),
        )
        return [r[0] for r in cur.fetchall()]

    # ---------- 重试 ----------
    def increment_retry(self, job_id: str) -> int:
        """retry_count += 1，返回新值。"""
        cur = self._conn.execute(
            "UPDATE jobs SET retry_count = retry_count + 1 WHERE id=?",
            (job_id,),
        )
        cur = self._conn.execute(
            "SELECT retry_count FROM jobs WHERE id=?", (job_id,),
        )
        return cur.fetchone()[0]

    def requeue(self, job_id: str) -> None:
        """running → queued 清空 started_at，用于 B 站 API 失败重试。"""
        self._conn.execute(
            "UPDATE jobs SET status=?, started_at=NULL WHERE id=?",
            (JobStatus.QUEUED.value, job_id),
        )

    # ---------- 计数 ----------
    def count(
        self,
        *,
        status: JobStatus | None = None,
    ) -> int:
        if status is not None:
            cur = self._conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status=?", (status.value,)
            )
        else:
            cur = self._conn.execute("SELECT COUNT(*) FROM jobs")
        return cur.fetchone()[0]

    # ---------- 清理 ----------
    def cleanup(
        self,
        *,
        status: JobStatus | None = None,
        older_than_seconds: float | None = None,
        all: bool = False,
        cascade: bool = True,
    ) -> int:
        """删除匹配条件的 jobs（含 job_logs 行）。

        参数（互斥；都不传 = 啥也不删，0）：
            status: 只删指定状态的
            older_than_seconds: 只删 finished_at 早于 now - N 的（其余状态不动）
            all: 不管状态全删
            cascade: 删完后再删 parent_id 指向被删项的 bv 子任务

        返回删除总数。
        """
        if status is None and older_than_seconds is None and not all:
            return 0

        where_clauses: list[str] = []
        params: list[Any] = []
        if all:
            pass  # no WHERE clause
        elif status is not None:
            where_clauses.append("status = ?")
            params.append(status.value)
        elif older_than_seconds is not None:
            threshold = time.time() - older_than_seconds
            where_clauses.append("finished_at IS NOT NULL AND finished_at < ?")
            params.append(threshold)

        where = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        # 找出将被删的 id（cascade 需要知道父 id 集合）
        cur = self._conn.execute(f"SELECT id FROM jobs{where}", params)
        target_ids = [r[0] for r in cur.fetchall()]
        if not target_ids:
            return 0

        def _placeholders_for(n: int) -> str:
            return ",".join("?" * n)

        # 先删 job_logs，避免外键孤行（虽然 schema 没 FK，但保持一致）
        self._conn.execute(
            f"DELETE FROM job_logs WHERE job_id IN ({_placeholders_for(len(target_ids))})",
            target_ids,
        )

        if cascade:
            # 把 parent_id IN (target_ids) 的 bv 子任务也加进来一起删
            cur = self._conn.execute(
                f"SELECT id FROM jobs WHERE parent_id IN ({_placeholders_for(len(target_ids))})",
                target_ids,
            )
            child_ids = [r[0] for r in cur.fetchall()]
            if child_ids:
                self._conn.execute(
                    f"DELETE FROM job_logs WHERE job_id IN ({_placeholders_for(len(child_ids))})",
                    child_ids,
                )
                target_ids.extend(child_ids)

        cur = self._conn.execute(
            f"DELETE FROM jobs WHERE id IN ({_placeholders_for(len(target_ids))})",
            target_ids,
        )
        return cur.rowcount

    # ---------- 内部 ----------
    def _row_to_dict(self, row: sqlite3.Row | tuple) -> dict[str, Any]:
        cols = (
            "id", "type", "target_id", "output_dir", "limit_n", "status",
            "parent_id", "result_path", "error",
            "created_at", "started_at", "finished_at", "retry_count",
        )
        return dict(zip(cols, row))

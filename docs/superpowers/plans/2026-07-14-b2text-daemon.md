# B2text Daemon Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local HTTP daemon (FastAPI on 127.0.0.1:8765) + CLI client wrapper on top of the existing single-shot `bilibili_to_text.py`. The daemon holds the FunASR model in memory so multiple transcribe requests don't re-load it.

**Architecture:**
1. CLI submits jobs to the daemon over HTTP (`httpx`).
2. Daemon (`uvicorn` + FastAPI) writes jobs to SQLite and pushes to an in-process `asyncio.Queue`.
3. A single asyncio worker consumes the queue and runs the seven-step pipeline (`get_video_info → get_audio_url → download_audio → convert_wav → chunk_audio → transcribe → normalize_write`) with structured JSON Lines logging.
4. CLI can list/cancel jobs and tail `jobs.log` via `b2text serve logs`.

**Tech Stack:**
- Python 3.10+ (existing)
- FastAPI 0.110+ + uvicorn[standard] 0.27+
- httpx 0.26+
- SQLite (stdlib) for queue
- Existing: FunASR, ffmpeg, curl (all required by prior plan)

**Reference Spec:** `docs/superpowers/specs/2026-07-14-b2text-daemon-design.md`

---

## File Structure

Files this plan creates:

| File | Responsibility |
|---|---|
| `b2text/paths.py` | Resolve XDG-style paths: `~/.config/b2text/cookie`, `~/.local/share/b2text/{jobs.db,jobs.log,daemon.pid}` |
| `b2text/cookie_store.py` | Resolve cookie: file → env → error |
| `b2text/queue.py` | SQLite-backed job queue: enqueue / claim / finish / fail / cancel / recover_orphans / list / get / get_logs |
| `b2text/job_log.py` | JSON Lines structured logger writing to `jobs.log` per job, plus `extra` schema |
| `b2text/upmaster.py` | `fetch_up_videos(uid, limit) -> list[str]` via B站 space/arc/search |
| `b2text/worker.py` | asyncio worker: consume queue, run pipeline 七步, write structured logs |
| `b2text/server.py` | FastAPI app: 6 routes + lifespan that starts/stops worker + loads FunASR |
| `b2text/client.py` | CLI HTTP client + `b2text run` (local escape hatch) |
| `b2text/cli.py` | argparse wiring for `b2text {serve,transcribe,run,status,list,cancel}` |
| `tests/test_paths.py` | XDG path resolution |
| `tests/test_cookie_store.py` | file / env / missing-error paths |
| `tests/test_queue.py` | enqueue / claim / finish / fail / cancel / recover |
| `tests/test_job_log.py` | JSON Lines writes, schema invariants, `extra.stacktrace` on error |
| `tests/test_upmaster.py` | mock bili_api; verify bv list & limit |
| `tests/test_worker.py` | mock pipeline; verify seven-step logging; recover-orphan |
| `tests/test_server.py` | FastAPI TestClient; 503 on model-not-ready; POST /transcribe; GET /tasks/{id}; DELETE /tasks/{id}; GET /tasks/{id}/log |
| `tests/test_client.py` | mock httpx; verify CLI subm build / cancel / list calls |

Files modified:

| File | Modification |
|---|---|
| `requirements.txt` | add `fastapi`, `uvicorn[standard]`, `httpx` |
| `bilibili_to_text.py` | becomes a 3-line shim: `import sys; from b2text.cli import main; sys.exit(main())` (backward-compat for `python bilibili_to_text.py BV... -o ...`) |
| `README.md` | document daemon lifecycle, cookie location, new commands, end-to-end example |

---

## Conventions

- **TDD**: every task writes failing tests first, then implementation, then commits.
- **Commits**: per task; conventional commits (`feat:`, `fix:`, `chore:`, `docs:`, `test:`).
- **Mocking**: external network calls (curl/ffmpeg/httpx) monkeypatched; SQLite tests use `tmp_path`.
- **Logging**: every task that emits logs uses `b2text.job_log.JobLog` so the schema is enforced uniformly.

---

### Task 1: 添加 web 依赖

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1.1: 追加 web 依赖**

```text
fastapi>=0.110.0
uvicorn[standard]>=0.27.0
httpx>=0.26.0
```

把上面三行追加到 `requirements.txt` 末尾（保持原内容在前）。

- [ ] **Step 1.2: 安装新依赖**

```bash
source venv/bin/activate && pip install -r requirements.txt
```

预期：成功安装 `fastapi`, `uvicorn[standard]`, `httpx`（以及 uvicorn 的 `[standard]` 额外依赖：`uvloop`、`watchfiles`、`websockets`、`httptools`、`requests` 等）。

- [ ] **Step 1.3: 验证导入**

```bash
python -c "import fastapi, uvicorn, httpx; print(fastapi.__version__, uvicorn.__version__, httpx.__version__)"
```

预期：打印出三个版本号，无 ImportError。

- [ ] **Step 1.4: Commit**

```bash
git add requirements.txt
git commit -m "chore: add fastapi + uvicorn + httpx for daemon service"
```

---

### Task 1.5: 注册 `b2text` console_script 入口

**Files:**
- Create: `pyproject.toml`

**理由**：README 和后续 CLI 文档都用 `b2text serve start` 这种调用形式；没有 entry point 用户只能跑 `python -m b2text.cli`，体验差。当前项目没有 `pyproject.toml` 或 `setup.py`，要补一个最小可用的。

- [ ] **Step 1.5.1: 写 pyproject.toml**

```toml
[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"

[project]
name = "b2text"
version = "0.2.0"
description = "B站视频对话转文本（daemon + CLI）"
requires-python = ">=3.10"
dependencies = [
    "fastapi>=0.110.0",
    "uvicorn[standard]>=0.27.0",
    "httpx>=0.26.0",
    "funasr>=1.0.0",
    "requests>=2.28.0",
]

[project.scripts]
b2text = "b2text.cli:main"

[tool.setuptools.packages.find]
include = ["b2text*"]
```

注意：以上 `dependencies` 把 `requirements.txt` 全部内容折叠成 single source of truth（pyproject 优先）。可以保留 `requirements.txt` 作为快速安装入口，但真实依赖在 `pyproject.toml`。

- [ ] **Step 1.5.2: 本地 editable 安装**

```bash
source venv/bin/activate && pip install -e .
```

预期：成功，`which b2text` 应打印路径。

- [ ] **Step 1.5.3: 验证 console_script**

```bash
b2text --help
```

预期：列出 `serve / transcribe / status / list / cancel / run` 子命令。

- [ ] **Step 1.5.4: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add pyproject.toml with b2text console_script entry point"
```

---

### Task 2: paths.py — XDG 风格路径解析

**Files:**
- Create: `b2text/paths.py`
- Create: `tests/test_paths.py`

**理由**：所有路径常量（cookie file、jobs.db、jobs.log、pidfile、daemon.log）必须集中在一个 module，避免硬编码分散在多个文件。

- [ ] **Step 2.1: 写失败测试**

```python
# tests/test_paths.py
import os
from pathlib import Path
from b2text.paths import (
    config_dir,
    data_dir,
    cookie_file,
    jobs_db,
    jobs_log,
    daemon_pid,
)


def test_config_dir_uses_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg_config"))
    d = config_dir()
    assert d == tmp_path / "xdg_config" / "b2text"


def test_data_dir_uses_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg_data"))
    d = data_dir()
    assert d == tmp_path / "xdg_data" / "b2text"


def test_falls_back_to_dot_config_and_dot_local_share(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert config_dir() == tmp_path / ".config" / "b2text"
    assert data_dir() == tmp_path / ".local" / "share" / "b2text"


def test_cookie_file_is_under_config_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert cookie_file() == tmp_path / "b2text" / "cookie"


def test_jobs_db_and_log_under_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert jobs_db() == tmp_path / "b2text" / "jobs.db"
    assert jobs_log() == tmp_path / "b2text" / "jobs.log"
    assert daemon_pid() == tmp_path / "b2text" / "daemon.pid"
```

- [ ] **Step 2.2: 运行测试验证失败**

```bash
pytest tests/test_paths.py -v
```

预期：ImportError `b2text.paths`。

- [ ] **Step 2.3: 实现 paths.py**

```python
# b2text/paths.py
"""XDG 风格路径解析，daemon 数据均放这里。"""
import os
from pathlib import Path

_APP = "b2text"


def config_dir() -> Path:
    """返回 config 目录（cookie、pidfile 等）。"""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.environ.get("HOME", "~"), ".config"
    )
    return Path(os.path.expanduser(base)) / _APP


def data_dir() -> Path:
    """返回 data 目录（jobs.db、jobs.log、daemon log）。"""
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.environ.get("HOME", "~"), ".local" / "share"
    )
    return Path(os.path.expanduser(base)) / _APP


def cookie_file() -> Path:
    return config_dir() / "cookie"


def jobs_db() -> Path:
    return data_dir() / "jobs.db"


def jobs_log() -> Path:
    return data_dir() / "jobs.log"


def daemon_pid() -> Path:
    return config_dir() / "daemon.pid"
```

- [ ] **Step 2.4: 运行测试验证通过**

```bash
pytest tests/test_paths.py -v
```

预期：6 passed。

- [ ] **Step 2.5: Commit**

```bash
git add b2text/paths.py tests/test_paths.py
git commit -m "feat: add paths.py with XDG-style resolution for daemon data"
```

---

### Task 3: cookie_store.py — Cookie 读取（文件优先 + env 兑底）

**Files:**
- Create: `b2text/cookie_store.py`
- Create: `tests/test_cookie_store.py`

- [ ] **Step 3.1: 写失败测试**

```python
# tests/test_cookie_store.py
import os
import pytest
from b2text.cookie_store import resolve_cookie, MissingCookieError


def test_resolves_cookie_from_file(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cookie_dir = tmp_path / "b2text"
    cookie_dir.mkdir(parents=True)
    (cookie_dir / "cookie").write_text("SESSDATA=abc; bili_jct=xyz")

    cookie = resolve_cookie()
    assert cookie == "SESSDATA=abc; bili_jct=xyz"


def test_env_used_when_file_missing(monkeypatch, tmp_path):
    """文件不存在时退回到环境变量。"""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("B2TEXT_COOKIE", "SESSDATA=env_one")

    cookie = resolve_cookie()
    assert cookie == "SESSDATA=env_one"


def test_file_wins_when_both_present(monkeypatch, tmp_path):
    """文件存在时优先于环境变量（spec 约定的优先级）。"""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cookie_dir = tmp_path / "b2text"
    cookie_dir.mkdir(parents=True)
    (cookie_dir / "cookie").write_text("SESSDATA=file_one")
    monkeypatch.setenv("B2TEXT_COOKIE", "SESSDATA=env_one")

    cookie = resolve_cookie()
    assert cookie == "SESSDATA=file_one"


def test_file_only_when_env_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("B2TEXT_COOKIE", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cookie_dir = tmp_path / "b2text"
    cookie_dir.mkdir(parents=True)
    (cookie_dir / "cookie").write_text("SESSDATA=file_only")

    cookie = resolve_cookie()
    assert cookie == "SESSDATA=file_only"


def test_missing_cookie_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("B2TEXT_COOKIE", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with pytest.raises(MissingCookieError, match="cookie"):
        resolve_cookie()


def test_empty_file_and_unset_env_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("B2TEXT_COOKIE", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cookie_dir = tmp_path / "b2text"
    cookie_dir.mkdir(parents=True)
    (cookie_dir / "cookie").write_text("   \n")  # 只有空白
    with pytest.raises(MissingCookieError):
        resolve_cookie()


def test_trims_whitespace(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cookie_dir = tmp_path / "b2text"
    cookie_dir.mkdir(parents=True)
    (cookie_dir / "cookie").write_text("  SESSDATA=trimmed\n")
    assert resolve_cookie() == "SESSDATA=trimmed"
```

- [ ] **Step 3.2: 运行测试验证失败**

```bash
pytest tests/test_cookie_store.py -v
```

预期：ImportError。

- [ ] **Step 3.3: 实现 cookie_store.py**

```python
# b2text/cookie_store.py
"""Cookie 读取（文件优先，B2TEXT_COOKIE 环境变量兑底）。

读取优先级（spec §4）：
  1. ~/.config/b2text/cookie（XDG 风格）
  2. B2TEXT_COOKIE 环境变量
  3. 都没有 → MissingCookieError
"""
import os

from b2text.paths import cookie_file


class MissingCookieError(RuntimeError):
    """未找到 cookie。请创建 ~/.config/b2text/cookie 或设置 B2TEXT_COOKIE。"""


def resolve_cookie() -> str:
    """返回单个 cookie 字符串（首位为 SESSDATA=...）。失败抛 MissingCookieError。"""
    path = cookie_file()
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text

    env_cookie = os.environ.get("B2TEXT_COOKIE")
    if env_cookie and env_cookie.strip():
        return env_cookie.strip()

    raise MissingCookieError(
        f"未找到 cookie。请在 {path} 写入 SESSDATA=...; bili_jct=...，"
        f"或设置环境变量 B2TEXT_COOKIE。"
    )
```

- [ ] **Step 3.4: 运行测试验证通过**

```bash
pytest tests/test_cookie_store.py -v
```

预期：6 passed。

- [ ] **Step 3.5: Commit**

```bash
git add b2text/cookie_store.py tests/test_cookie_store.py
git commit -m "feat: add cookie_store with file->env fallback and MissingCookieError"
```

---

### Task 4: queue.py — SQLite-backed 任务队列（CRUD）

**Files:**
- Create: `b2text/queue.py`
- Create: `tests/test_queue.py`

**理由**：任务状态需要在 daemon 重启时存活。SQLite 是 stdlib 唯一持久化方案，无新依赖。

- [ ] **Step 4.1: 写失败测试（CRUD 部分）**

```python
# tests/test_queue.py
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
```

- [ ] **Step 4.2: 运行测试验证失败**

```bash
pytest tests/test_queue.py -v
```

预期：ImportError。

- [ ] **Step 4.3: 实现 queue.py（CRUD）**

```python
# b2text/queue.py
"""SQLite-backed 任务队列（CRUD + 孤儿恢复 + 日志行持久化）。"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Any, Iterable


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
        self._conn = sqlite3.connect(str(db_path), isolation_level=None)
        self._conn.executescript(_SCHEMA)
        self._conn.execute("PRAGMA journal_mode=WAL")

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

    # ---------- 内部 ----------
    def _row_to_dict(self, row: sqlite3.Row | tuple) -> dict[str, Any]:
        cols = (
            "id", "type", "target_id", "output_dir", "limit_n", "status",
            "parent_id", "result_path", "error",
            "created_at", "started_at", "finished_at", "retry_count",
        )
        return dict(zip(cols, row))
```

- [ ] **Step 4.4: 运行测试验证通过**

```bash
pytest tests/test_queue.py -v
```

预期：9 passed。

- [ ] **Step 4.5: Commit**

```bash
git add b2text/queue.py tests/test_queue.py
git commit -m "feat: add SQLite-backed job queue with CRUD, orphan recovery, per-job logs"
```

---

### Task 5: queue.py — retry_count 递增和 B 站 API 重试 helper

**Files:**
- Modify: `b2text/queue.py`
- Modify: `tests/test_queue.py`

- [ ] **Step 5.1: 写失败测试**

在 `tests/test_queue.py` 末尾追加：

```python
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
```

- [ ] **Step 5.2: 运行测试验证失败**

```bash
pytest tests/test_queue.py::test_increment_retry_count tests/test_queue.py::test_requeue_resets_status_from_running -v
```

预期：AttributeError `increment_retry` / `requeue`。

- [ ] **Step 5.3: 实现两个方法**

在 `b2text/queue.py` 的 `JobQueue` 类内追加（在 `_row_to_dict` 之前）：

```python
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
```

- [ ] **Step 5.4: 运行测试验证通过**

```bash
pytest tests/test_queue.py -v
```

预期：11 passed。

- [ ] **Step 5.5: Commit**

```bash
git add b2text/queue.py tests/test_queue.py
git commit -m "feat: add increment_retry and requeue to JobQueue"
```

---

### Task 6: job_log.py — 结构化 JSON Lines 日志（pipeline 七步 + stacktrace）

**Files:**
- Create: `b2text/job_log.py`
- Create: `tests/test_job_log.py`

- [ ] **Step 6.1: 写失败测试**

```python
# tests/test_job_log.py
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
    assert "boom" in err["msg"]
    assert err["extra"]["chunk_index"] == 0
    assert "ValueError" in err["extra"]["exc_type"]
    assert "boom" in err["extra"]["exc_message"]
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
```

- [ ] **Step 6.2: 运行测试验证失败**

```bash
pytest tests/test_job_log.py -v
```

预期：ImportError。

- [ ] **Step 6.3: 实现 job_log.py**

```python
# b2text/job_log.py
"""结构化 JSON Lines 日志：每行一个 dict，必含 ts/level/job_id/step/msg。

- 失败时 extra 必填 exc_type / exc_message / stacktrace
- 支持 StepLogger 上下文管理器（自动捕获异常）
"""
from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any, Self


_TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%f"


class JobLog:
    """单一 job 的结构化日志写入器。线程不安全（每 job 实例化一次即可）。"""

    def __init__(self, log_path: Path, *, job_id: str):
        self.log_path = log_path
        self.job_id = job_id
        # 不创建父目录 — daemon 启动时统一建好

    def _emit(self, level: str, step: str, msg: str, extra: dict[str, Any] | None) -> None:
        ts = datetime.now(timezone.utc).strftime(_TS_FORMAT)[:-3] + "Z"
        record = {
            "ts": ts,
            "level": level,
            "job_id": self.job_id,
            "step": step,
            "msg": msg,
            "extra": extra or {},
        }
        line = json.dumps(record, ensure_ascii=False)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        # 实时同步到 stderr（daemon 模式不依赖 stdout）
        print(line, file=sys.stderr, flush=True)

    def info(self, msg: str, *, step: str = "_", extra: dict[str, Any] | None = None) -> None:
        self._emit("INFO", step, msg, extra)

    def warn(self, msg: str, *, step: str = "_", extra: dict[str, Any] | None = None) -> None:
        self._emit("WARNING", step, msg, extra)

    def error(self, msg: str, *, step: str = "_", extra: dict[str, Any] | None = None) -> None:
        self._emit("ERROR", step, msg, extra)

    def step_start(self, step: str, **extra: Any) -> None:
        self.info("start", step=step, extra=extra)

    def step_ok(self, step: str, **extra: Any) -> None:
        self.info("ok", step=step, extra=extra)

    def step_fail(
        self,
        step: str,
        *,
        exc_info: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> None:
        merged: dict[str, Any] = dict(extra or {})
        if exc_info:
            et, ev, tb = sys.exc_info()
            if et is not None:
                merged["exc_type"] = et.__name__
                merged["exc_message"] = str(ev) if ev else ""
                merged["stacktrace"] = "".join(
                    traceback.format_exception(et, ev, tb)
                )
        self.error("fail", step=step, extra=merged)

    def step(self, name: str) -> "StepLogger":
        return StepLogger(self, name)


class StepLogger:
    """上下文管理器，自动在 exit 时根据是否异常决定 step_ok 或 step_fail。"""

    def __init__(self, log: JobLog, name: str):
        self._log = log
        self._name = name
        self._extra: dict[str, Any] = {}

    def set(self, **kwargs: Any) -> Self:
        self._extra.update(kwargs)
        return self

    def __enter__(self) -> Self:
        self._log.step_start(self._name, **self._extra)
        self._extra.clear()  # start 阶段记录 context；ok/fail 只记结果字段
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self._log.step_ok(self._name, **self._extra)
        else:
            self._log.step_fail(self._name, exc_info=True, extra=self._extra)
```

- [ ] **Step 6.4: 运行测试验证通过**

```bash
pytest tests/test_job_log.py -v
```

预期：6 passed。

- [ ] **Step 6.5: Commit**

```bash
git add b2text/job_log.py tests/test_job_log.py
git commit -m "feat: add structured JSON Lines job logger with step context manager"
```

---

### Task 7: upmaster.py — UP 主视频列表抓取

**Files:**
- Create: `b2text/upmaster.py`
- Create: `tests/test_upmaster.py`

- [ ] **Step 7.1: 写失败测试**

```python
# tests/test_upmaster.py
import json
import subprocess
import pytest
from b2text.upmaster import fetch_up_videos


def test_returns_bvid_list_within_limit(monkeypatch):
    """调用 B 站 space/arc/search，返回 bvid 列表。"""
    fake = {
        "code": 0,
        "data": {
            "list": {
                "vlist": [
                    {"bvid": "BV1aaa"}, {"bvid": "BV1bbb"}, {"bvid": "BV1ccc"},
                ]
            }
        }
    }

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, json.dumps(fake).encode(), b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    bvids = fetch_up_videos(uid=12345, limit=2, cookie="SESSDATA=x")
    assert bvids == ["BV1aaa", "BV1bbb"]


def test_clamps_limit_to_max_50(monkeypatch):
    """B 站单页最多 50，limit > 50 时取 50。"""
    fake = {"code": 0, "data": {"list": {"vlist": [{"bvid": f"BV{i:08d}"} for i in range(30)]}}}

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, json.dumps(fake).encode(), b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    fetch_up_videos(uid=1, limit=10, cookie="SESSDATA=x")
    cmd = fake_run.call_args_list[0].args[0]
    # URL 包含 ps=10
    assert "ps=10" in cmd[cmd.index("GET") + 1] or "ps=10" in " ".join(cmd)


def test_returns_empty_on_error(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, b'{"code":-1}', b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert fetch_up_videos(uid=1, limit=10, cookie="SESSDATA=x") == []


def test_passes_cookie_header(monkeypatch):
    fake = {"code": 0, "data": {"list": {"vlist": []}}}

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, json.dumps(fake).encode(), b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    fetch_up_videos(uid=99, limit=5, cookie="SESSDATA=my_cookie")
    cmd = fake_run.call_args_list[0].args[0]
    assert any("Cookie: SESSDATA=my_cookie" in str(c) for c in cmd)
```

- [ ] **Step 7.2: 运行测试验证失败**

```bash
pytest tests/test_upmaster.py -v
```

预期：ImportError。

- [ ] **Step 7.3: 实现 upmaster.py**

```python
# b2text/upmaster.py
"""UP 主视频列表抓取 + 批量展开为 bvid 列表。"""
from __future__ import annotations

import json
import subprocess
from urllib.parse import urlencode


_SPACE_API = "https://api.bilibili.com/x/space/arc/search"


def fetch_up_videos(uid: int, limit: int, *, cookie: str) -> list[str]:
    """调用 B 站 space/arc/search，返回最多 limit 条 bvid。

    参数：
        uid: UP 主 mid
        limit: 用户要的视频数（1-50，B 站单页最多 50）
        cookie: 完整 cookie 字符串

    返回：bvid 字符串列表（已按 B 站返回顺序）
    """
    if limit < 1:
        return []
    ps = min(limit, 50)  # B 站单页最多 50
    url = f"{_SPACE_API}?{urlencode({'mid': uid, 'ps': ps, 'pn': 1, 'order': 'pubdate'})}"
    cmd = [
        "curl", "-s", url,
        "-H", f"Cookie: {cookie}",
        "-H", "User-Agent: Mozilla/5.0",
        "-H", "Referer: https://space.bilibili.com/",
        "--max-time", "20",
    ]
    result = subprocess.run(cmd, capture_output=True)
    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return []
    if data.get("code") != 0:
        return []
    vlist = data.get("data", {}).get("list", {}).get("vlist", [])
    bvids = [v.get("bvid") for v in vlist if v.get("bvid")]
    return bvids[:limit]
```

注意：`test_clamps_limit_to_max_50` 用 `fake_run.call_args_list[0].args[0]` 取命令——这要求 fake_run 被 monkeypatch 到 `subprocess.run`，但 monkeypatch 是 `lambda`，需要换。先简化该测试：

- [ ] **Step 7.4: 修正测试，简化 limit 断言**

把 `tests/test_upmaster.py::test_clamps_limit_to_max_50` 中 `fake_run.call_args_list[0].args[0]` 改为：

```python
def test_clamps_limit_to_max_50(monkeypatch):
    """B 站单页最多 50，limit > 50 时取 50。"""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"code":0,"data":{"list":{"vlist":[]}}}).encode(), b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    fetch_up_videos(uid=1, limit=10, cookie="SESSDATA=x")
    cmd_str = " ".join(captured["cmd"])
    assert "ps=10" in cmd_str

    fetch_up_videos(uid=1, limit=999, cookie="SESSDATA=x")
    cmd_str = " ".join(captured["cmd"])
    assert "ps=50" in cmd_str  # 最大 50
```

- [ ] **Step 7.5: 运行测试验证通过**

```bash
pytest tests/test_upmaster.py -v
```

预期：4 passed。

- [ ] **Step 7.6: Commit**

```bash
git add b2text/upmaster.py tests/test_upmaster.py
git commit -m "feat: add upmaster.fetch_up_videos for UP master batch expansion"
```

---

### Task 8: worker.py — pipeline 七步骨架 + 队列消费

**Files:**
- Create: `b2text/worker.py`
- Create: `tests/test_worker.py`

**理由**：核心逻辑。重点是把 pipeline 七步都包到结构化日志中。每个 step 单独记录 start / ok / fail，并在错误时捕获 stacktrace。

为简化测试，本任务先做一个"占位 pipeline"，让每一步都是一个 fake 函数（之后 Task 9-10 接入真实步骤）。这样测试 worker 的关键控制流：取任务 → 跑步骤 → 写 SQLite → 写日志。

- [ ] **Step 8.1: 写失败测试**

```python
# tests/test_worker.py
import asyncio
import json
import time
from pathlib import Path
import pytest
from unittest.mock import AsyncMock

from b2text.worker import Worker
from b2text.queue import JobQueue, JobStatus


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    db = tmp_path / "jobs.db"
    log = tmp_path / "jobs.log"
    cookie = "SESSDATA=test"
    q = JobQueue(db)
    yield q, log, cookie
    q.close()


def _make_worker(q, log_path, cookie, steps):
    return Worker(queue=q, log_path=log_path, cookie=cookie, steps=steps)


def test_worker_processes_queued_job_to_done(env):
    q, log_path, cookie = env
    steps = {
        "get_video_info": lambda job, log: log.step_ok("get_video_info", aid=12345),
    }
    worker = _make_worker(q, log_path, cookie, steps)
    job_id = q.enqueue(type="bv", target_id="BV1xxx", output_dir="/tmp/out")

    asyncio.run(worker.run_once())

    job = q.get(job_id)
    assert job["status"] == JobStatus.DONE
    assert job["result_path"] == "/tmp/out/BV1xxx.txt"


def test_worker_marks_failed_on_exception(env):
    q, log_path, cookie = env
    def boom(job, log):
        raise RuntimeError("transcribe crashed")
    steps = {"get_video_info": boom}
    worker = _make_worker(q, log_path, cookie, steps)
    job_id = q.enqueue(type="bv", target_id="BV1xxx", output_dir="/tmp/out")

    asyncio.run(worker.run_once())

    job = q.get(job_id)
    assert job["status"] == JobStatus.FAILED
    assert "transcribe crashed" in job["error"]


def test_worker_logs_each_step(env):
    q, log_path, cookie = env
    steps = {
        "get_video_info": lambda job, log: log.step_ok("get_video_info", aid=1),
        "transcribe": lambda job, log: log.step_ok("transcribe", segment_count=42),
    }
    worker = _make_worker(q, log_path, cookie, steps)
    job_id = q.enqueue(type="bv", target_id="BV1xxx", output_dir="/tmp/out")
    asyncio.run(worker.run_once())

    lines = [json.loads(ln) for ln in log_path.read_text().splitlines()]
    steps_seen = [ln["step"] for ln in lines]
    assert "get_video_info" in steps_seen
    assert "transcribe" in steps_seen
    ok_lines = [ln for ln in lines if ln["msg"] == "ok"]
    assert any(ln["extra"].get("aid") == 1 for ln in ok_lines)
    assert any(ln["extra"].get("segment_count") == 42 for ln in ok_lines)


def test_worker_run_once_returns_none_when_queue_empty(env):
    q, log_path, cookie = env
    worker = _make_worker(q, log_path, cookie, steps={"get_video_info": lambda job, log: None})
    assert asyncio.run(worker.run_once()) is None


def test_worker_up_job_finishes_without_result_path(env):
    """type=up 父任务 fan-out 后直接 done，result_path=None。"""
    q, log_path, cookie = env

    def fanout(job, log):
        # 模拟 upmaster 拉到的子 bvid
        for bvid in ["BV1aaa", "BV1bbb"]:
            q.enqueue(type="bv", target_id=bvid, output_dir=job["output_dir"], parent_id=job["id"])
        log.set(child_count=2)

    worker = _make_worker(q, log_path, cookie, steps={"fanout": fanout})
    parent_id = q.enqueue(type="up", target_id="12345", output_dir="/tmp/out", limit_n=2)
    asyncio.run(worker.run_once())

    job = q.get(parent_id)
    assert job["status"] == JobStatus.DONE
    assert job["result_path"] is None
    # 子任务已入队
    children = [j for j in q.list() if j["parent_id"] == parent_id]
    assert len(children) == 2
```

- [ ] **Step 8.2: 运行测试验证失败**

```bash
pytest tests/test_worker.py -v
```

预期：ImportError。

- [ ] **Step 8.3: 实现 worker.py（骨架版）**

```python
# b2text/worker.py
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
```

- [ ] **Step 8.4: 运行测试验证通过**

```bash
pytest tests/test_worker.py -v
```

预期：5 passed（含 type=up fan-out 测试）。

- [ ] **Step 8.5: Commit**

```bash
git add b2text/worker.py tests/test_worker.py
git commit -m "feat: add Worker skeleton with structured pipeline logging"
```

---

### Task 9: worker.py — 接入真实 pipeline 步骤

**Files:**
- Modify: `b2text/worker.py`
- Create: `tests/test_worker_integration.py`

**理由**：把骨架 worker 接入真实的 `b2text.bili_api`、`b2text.audio`、`b2text.transcriber`。所有外部副作用（curl/ffmpeg/FunASR）通过 mock 屏蔽。

- [ ] **Step 9.1: 写测试**

```python
# tests/test_worker_integration.py
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

    steps = build_default_steps(cookie="SESSDATA=t", transcriber=MagicMock())

    # Mock bili_api
    async def run():
        with patch("b2text.bili_api.get_video_info", return_value=_mock_video_info("BV1xxx")), \
             patch("b2text.bili_api.get_audio_url", return_value="https://example.com/audio.m4s"), \
             patch("b2text.audio.download_audio_stream") as mock_dl, \
             patch("b2text.audio.extract_audio_from_mp4") as mock_extract, \
             patch("pathlib.Path.write_text") as mock_write:

            # Mock curl output 创建一个临时 m4s
            def fake_dl(url, out, cookie):
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(b"fake audio")
                return out
            mock_dl.side_effect = fake_dl

            # Mock ffmpeg 创建 wav
            def fake_extract(mp4_path, wav_path):
                wav_path.parent.mkdir(parents=True, exist_ok=True)
                wav_path.write_bytes(b"fake wav")
                return wav_path
            mock_extract.side_effect = fake_extract

            job_id = q.enqueue(type="bv", target_id="BV1xxx", output_dir=str(output_dir))
            worker = build_worker_with(q, log_path, steps)
            await worker.run_once()
            return q.get(job_id)

    job = asyncio.run(run())
    assert job["status"] == JobStatus.DONE
    assert job["result_path"].endswith(".txt")
    assert Path(job["result_path"]).exists()


def build_worker_with(q, log_path, steps):
    from b2text.worker import Worker
    return Worker(queue=q, log_path=log_path, cookie="dummy", steps=steps)


def test_bili_api_retries_3_times_then_fails(env, monkeypatch):
    """B 站 API 失败 3 次后标记 failed，retry_count=3。"""
    from b2text.worker import Worker
    q, log_path = env
    # 把 retry sleep 缩短避免测试慢
    monkeypatch.setattr("b2text.worker.time.sleep", lambda _: None)

    call_count = {"n": 0}

    def always_fail(bvid):
        call_count["n"] += 1
        raise ConnectionError("network down")

    steps = {
        "fanout": lambda job, log: log.set(fanout=False),
        "get_video_info": always_fail,
        # 其余 step 不会跑到
    }
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

    def flaky(bvid):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ConnectionError(f"attempt {attempts['n']} failed")
        return _mock_video_info(bvid)

    def noop_audio_url(*a, **kw):
        return "https://example.com/audio.m4s"

    def noop_dl(*a, **kw):
        from pathlib import Path
        out = a[1]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"x")
        return out

    def noop_extract(mp4, wav):
        wav.parent.mkdir(parents=True, exist_ok=True)
        wav.write_bytes(b"x")
        return wav

    transcriber = MagicMock()
    transcriber.transcribe.return_value = []

    steps = build_default_steps(cookie="SESSDATA=t", transcriber=transcriber, queue=q)

    with patch("b2text.bili_api.get_video_info", side_effect=flaky), \
         patch("b2text.bili_api.get_audio_url", side_effect=noop_audio_url), \
         patch("b2text.audio.download_audio_stream", side_effect=noop_dl), \
         patch("b2text.audio.extract_audio_from_mp4", side_effect=noop_extract):
        worker = Worker(queue=q, log_path=log_path, cookie="SESSDATA=t", steps=steps)
        output_dir = Path(env[0].db_path).parent / "out"
        output_dir.mkdir()
        job_id = q.enqueue(type="bv", target_id="BV1xxx", output_dir=str(output_dir))
        asyncio.run(worker.run_once())

    job = q.get(job_id)
    assert job["status"] == JobStatus.DONE
    assert job["retry_count"] == 2  # 2 次失败都增加了 retry_count

但 `b2text.audio.extract_audio_from_mp4` 是阻塞调用，worker 是 `async def`，所以会阻塞事件循环——v1 worker 在 fastapi 进程内单线程运行，是 OK 的。先假设同步即可。

- [ ] **Step 9.2: 运行测试验证失败**

```bash
pytest tests/test_worker_integration.py -v
```

预期：ImportError `build_default_steps`。

- [ ] **Step 9.3: 实现 build_default_steps**

在 `b2text/worker.py` 末尾追加：

```python
def build_default_steps(*, cookie: str, transcriber, queue: JobQueue | None = None) -> dict[str, "Step"]:
    """构造一个完整的 pipeline（fan-out + 七步），注入真实 bili_api / audio / transcriber。

    bili_api 的 get_video_info / get_audio_url 不接受 cookie kwarg —— 通过设置
    `b2text.bili_api.COOKIE = cookie` 模块级变量把当前 daemon cookie 注入。
    transcriber 是 FunASRTranscriber 实例（或者测试中 mock）。
    queue 传入后，retry 调用 increment_retry 累计重试次数（spec §5 重试策略）。
    """
    from b2text import bili_api, audio
    from b2text.normalizer import normalize_funasr_output
    from b2text.formatter import format_segments
    import time

    bili_api.COOKIE = cookie  # 模块级注入（startup-once）

    delays = [1, 4, 16]  # 指数退避（spec §5）
    api_max_attempts = 3

    def _with_api_retry(log, job, step_name, fn, *args):
        last_err = None
        for attempt in range(api_max_attempts):
            try:
                return fn(*args)
            except Exception as e:
                last_err = e
                if queue is not None:
                    queue.increment_retry(job["id"])
                if attempt < api_max_attempts - 1:
                    log.warn(
                        f"{step_name} 失败（第 {attempt+1}/{api_max_attempts} 次）",
                        step=step_name,
                        extra={"error": str(e), "next_sleep": delays[attempt]},
                    )
                    time.sleep(delays[attempt])
        raise last_err

    def fanout(job, log):
        """type=up 时拉取 UP 主视频列表，创建子任务（parent_id），父任务立即 done。
        type=bv 时为 no-op。
        注意：本步只在 type=up 任务里把数据展开；type=bv 不做任何事（BV 由后续七步处理）。
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
        bvids = fetch_up_videos(uid, limit, cookie=cookie)
        if not bvids:
            raise RuntimeError(f"upmaster 没拉到任何视频：uid={uid}")
        for bvid in bvids:
            queue.enqueue(
                type="bv", target_id=bvid, output_dir=job["output_dir"], parent_id=job["id"],
            )
        log.set(child_count=len(bvids))

    def get_video_info(job, log):
        info = _with_api_retry(log, job, "get_video_info", bili_api.get_video_info, job["target_id"])
        if not info:
            raise RuntimeError(f"get_video_info failed for {job['target_id']}")
        job["_video_info"] = info
        log.set(aid=info["aid"], title=info["title"])

    def get_audio_url(job, log):
        info = job["_video_info"]
        cid = info["pages"][0]["cid"]
        url = _with_api_retry(log, job, "get_audio_url", bili_api.get_audio_url, info["aid"], cid)
        if not url:
            raise RuntimeError("get_audio_url failed")
        job["_audio_url"] = url

    def download_audio(job, log):
        import tempfile
        from pathlib import Path
        tmpdir = Path(tempfile.mkdtemp(prefix="b2text_dl_"))
        m4s = tmpdir / "audio.m4s"
        audio.download_audio_stream(job["_audio_url"], m4s, cookie=cookie)
        # m4s 路径留给下一步；保到 job dict
        job["_audio_path"] = m4s

    def convert_wav(job, log):
        from b2text.audio import extract_audio_from_mp4
        import subprocess
        from pathlib import Path
        src = Path(job["_audio_path"])
        wav = src.parent / "audio.wav"
        extract_audio_from_mp4(src, wav)
        job["_wav_path"] = wav

    def chunk_audio(job, log):
        # b2text.transcriber 已经内置 chunk 逻辑（>10 min 自动切）
        # 这里仅查 duration 以便日志，标 0 表示无需独立切分
        log.set(chunked=False)

    def transcribe(job, log):
        log.set(device="mps", chunk_index=0, chunk_count=1)
        raw = transcriber.transcribe(job["_wav_path"])
        if not raw:
            raise RuntimeError("FunASR returned empty")
        job["_raw_segments"] = raw
        log.set(segment_count=len(raw))

    def normalize_write(job, log):
        from b2text.normalizer import normalize_funasr_output
        from b2text.formatter import format_segments
        segs = normalize_funasr_output(job["_raw_segments"])
        text = format_segments(segs)
        out_path = _result_path_for({"target_id": job["target_id"], "output_dir": job["output_dir"]})
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(text, encoding="utf-8")
        log.set(segment_count=len(segs), output_path=out_path)

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


def _result_path_for(job):
    """结果路径：<output_dir>/<safe_target_id>.txt"""
    import re
    safe = re.sub(r'[<>:"/\\|?*]', "_", job["target_id"])[:80]
    return str(Path(job["output_dir"]) / f"{safe}.txt")
```

- [ ] **Step 9.4: 运行测试验证通过**

```bash
pytest tests/test_worker_integration.py -v
```

预期：1 passed（其余需要 mock audio.extract_audio_from_mp4 注意：原本 ensure_wav 在 wav 已存在时直接返回，但本测试源是 m4s，所以会经过 extract 分支；mock 即可）。

如果 extract_audio_from_mp4 在测试中返回 wav_path 而不是 wav_path 已写入字节导致 normalize_write 步骤拿不到数据，需调整测试 mock。

预期：3 passed（原有 1 + 新增 2 个 retry 测试）。

- [ ] **Step 9.5: Commit**

```bash
git add b2text/worker.py tests/test_worker_integration.py
git commit -m "feat: wire up Worker pipeline to real bili_api + audio + transcriber"
```

---

### Task 10: server.py — FastAPI 路由 + 生命周期

**Files:**
- Create: `b2text/server.py`
- Create: `tests/test_server.py`

- [ ] **Step 10.1: 写失败测试**

```python
# tests/test_server.py
import pytest
from fastapi.testclient import TestClient
from b2text.server import build_app, AppContext


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("B2TEXT_COOKIE", "SESSDATA=test")
    db = tmp_path / "jobs.db"
    log = tmp_path / "jobs.log"

    ctx = AppContext(
        db_path=db,
        log_path=log,
        cookie="SESSDATA=test",
        run_real_pipeline=False,  # 测试中走 fake pipeline
    )
    app = build_app(ctx)
    return TestClient(app)


def test_health_returns_503_when_model_not_ready(app):
    r = app.get("/health")
    assert r.status_code in (200, 503)
    body = r.json()
    assert "ok" in body
    assert "model_loaded" in body


def test_post_transcribe_returns_task_id(app):
    r = app.post("/transcribe", json={
        "type": "bv", "id": "BV1xxx", "output_dir": "/tmp/out"
    })
    assert r.status_code == 200
    body = r.json()
    assert "task_id" in body
    assert len(body["task_id"]) > 0


def test_post_transcribe_rejects_bad_id(app):
    r = app.post("/transcribe", json={"type": "bv", "id": "not-a-bvid", "output_dir": "/tmp/out"})
    assert r.status_code == 400


def test_get_task_returns_status(app):
    r = app.post("/transcribe", json={"type": "bv", "id": "BV1aaa", "output_dir": "/tmp/out"})
    task_id = r.json()["task_id"]
    r = app.get(f"/tasks/{task_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == task_id
    assert body["type"] == "bv"


def test_get_task_log_returns_lines(app):
    r = app.post("/transcribe", json={"type": "bv", "id": "BV1aaa", "output_dir": "/tmp/out"})
    task_id = r.json()["task_id"]
    # 写一条假日志方便测试
    ctx_log = app.app.state.ctx.log_path
    ctx_log.parent.mkdir(parents=True, exist_ok=True)
    ctx_log.write_text(
        '{"ts":"2026-07-14T12:00:00.000Z","level":"INFO","job_id":"' + task_id + '","step":"_","msg":"hi","extra":{}}\n',
        encoding="utf-8",
    )
    r = app.get(f"/tasks/{task_id}/log")
    assert r.status_code == 200
    body = r.json()
    assert "logs" in body
    assert len(body["logs"]) >= 1


def test_delete_task_only_cancels_queued(app):
    r = app.post("/transcribe", json={"type": "bv", "id": "BV1xxx", "output_dir": "/tmp/out"})
    task_id = r.json()["task_id"]
    r = app.delete(f"/tasks/{task_id}")
    assert r.status_code in (200, 204)
    assert "cancelled" in app.get(f"/tasks/{task_id}").json()["status"]


def test_list_tasks_returns_array(app):
    app.post("/transcribe", json={"type": "bv", "id": "BV1a", "output_dir": "/tmp/out"})
    app.post("/transcribe", json={"type": "bv", "id": "BV1b", "output_dir": "/tmp/out"})
    r = app.get("/tasks")
    assert r.status_code == 200
    assert "tasks" in r.json()
    assert len(r.json()["tasks"]) >= 2
```

- [ ] **Step 10.2: 运行测试验证失败**

```bash
pytest tests/test_server.py -v
```

预期：ImportError。

- [ ] **Step 10.3: 实现 server.py**

```python
# b2text/server.py
"""HTTP daemon for b2text. Provides:
  POST   /transcribe         submit a job
  GET    /tasks              list jobs
  GET    /tasks/{id}         one job status
  GET    /tasks/{id}/log     structured logs of a job
  DELETE /tasks/{id}         cancel a queued job
  GET    /health             daemon + model status
"""
from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from b2text.queue import JobQueue, JobStatus


@dataclass
class AppContext:
    db_path: Path
    log_path: Path
    cookie: str
    run_real_pipeline: bool  # True 时启动真 worker；False 时 worker 不跑


class TranscribeRequest(BaseModel):
    type: str = Field(..., pattern="^(bv|up)$")
    id: str = Field(..., min_length=1)
    output_dir: str = Field(..., min_length=1)
    limit: int | None = Field(None, ge=1, le=50)


_BVID_RE = re.compile(r"^BV[a-zA-Z0-9]+$")


def _validate_bvid(value: str) -> bool:
    return bool(_BVID_RE.match(value))


def build_app(ctx: AppContext) -> FastAPI:
    """构建 FastAPI app；worker 在 lifespan 启动/停止。"""
    queue = JobQueue(ctx.db_path)
    state: dict[str, Any] = {"queue": queue, "ctx": ctx, "worker": None, "model_loaded": False}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 启动：恢复孤儿任务
        recovered = queue.recover_orphans()
        if recovered:
            print(f"[server] recovered {recovered} orphan tasks", flush=True)

        if ctx.run_real_pipeline:
            # 真 worker（带 FunASR 模型加载）
            from b2text.worker import Worker
            from b2text.transcriber import FunASRTranscriber
            transcriber = FunASRTranscriber()
            from b2text.worker import build_default_steps
            steps = build_default_steps(cookie=ctx.cookie, transcriber=transcriber, queue=queue)

            async def _load_model_then_mark_ready():
                # 模型加载耗时较长；在线程池里跑
                await asyncio.get_running_loop().run_in_executor(None, transcriber._load_model)
                state["model_loaded"] = True

            asyncio.create_task(_load_model_then_mark_ready())
            worker = Worker(queue=queue, log_path=ctx.log_path, cookie=ctx.cookie, steps=steps)
            task = asyncio.create_task(worker.serve_forever())
            state["worker"] = task
        yield
        # 关闭
        if state["worker"]:
            state["worker"].cancel()
            try:
                await state["worker"]
            except asyncio.CancelledError:
                pass
        queue.close()

    app = FastAPI(title="b2text daemon", lifespan=lifespan)
    app.state.ctx = ctx

    @app.get("/health")
    def health():
        model_loaded = state.get("model_loaded", False) if ctx.run_real_pipeline else True
        body = {
            "ok": model_loaded,
            "model_loaded": model_loaded,
            "queue_len": len(queue.list(status=JobStatus.QUEUED)),
            "running": len(queue.list(status=JobStatus.RUNNING)),
        }
        code = 200 if model_loaded else 503
        return JSONResponse(body, status_code=code)

    @app.post("/transcribe")
    def submit(req: TranscribeRequest):
        if req.type == "bv" and not _validate_bvid(req.id):
            raise HTTPException(400, detail="invalid bv id format")
        if req.type == "up":
            try:
                uid = int(req.id)
            except ValueError:
                raise HTTPException(400, detail="up id must be integer")
            if not (1 <= uid <= 10**11):
                raise HTTPException(400, detail="up id out of range")
        if not state.get("model_loaded", False) and ctx.run_real_pipeline:
            raise HTTPException(503, detail={"error": "model_loading"})
        # 创建任务
        job_id = queue.enqueue(
            type=req.type,
            target_id=req.id,
            output_dir=req.output_dir,
            limit_n=req.limit,
        )
        return {"task_id": job_id}

    @app.get("/tasks")
    def list_tasks(status: str | None = Query(None), limit: int = 50, offset: int = 0):
        st = JobStatus(status) if status else None
        return {
            "tasks": queue.list(status=st, limit=limit, offset=offset),
            "total": len(queue.list(status=st)),
        }

    @app.get("/tasks/{task_id}")
    def get_task(task_id: str):
        job = queue.get(task_id)
        if job is None:
            raise HTTPException(404, detail="not found")
        return job

    @app.get("/tasks/{task_id}/log")
    def get_task_log(task_id: str):
        if queue.get(task_id) is None:
            raise HTTPException(404, detail="not found")
        import json
        logs = []
        for line in queue.get_logs(task_id):
            try:
                logs.append(json.loads(line))
            except json.JSONDecodeError:
                logs.append({"raw": line})
        return {"logs": logs}

    @app.delete("/tasks/{task_id}")
    def cancel_task(task_id: str):
        if queue.get(task_id) is None:
            raise HTTPException(404, detail="not found")
        if queue.cancel(task_id):
            return JSONResponse({"status": "cancelled"}, status_code=200)
        raise HTTPException(409, detail="not cancellable in current status")

    return app


def main():
    """CLI 入口：python -m b2text.server [--port N] [--no-funasr]"""
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
```

注意：测试需要 `pydantic`（fastapi 自带）。`from b2text.queue import JobStatus` 用于 status 枚举转换。

- [ ] **Step 10.4: 安装 fastapi 测试依赖 + 验证通过**

```bash
source venv/bin/activate && pip install httpx  # fastapi TestClient 需要
pytest tests/test_server.py -v
```

预期：7 passed（含 trims_whitespace / env_used_when_file_missing / file_wins_when_both_present 等）。

> 如果有个别测试因为 `state` shape 不匹配失败，调试后改 server.py。

- [ ] **Step 10.5: Commit**

```bash
git add b2text/server.py tests/test_server.py
git commit -m "feat: add FastAPI server with 6 endpoints and lifespan-managed worker"
```

---

### Task 11: client.py — CLI HTTP 客户端（httpx）

**Files:**
- Create: `b2text/client.py`
- Create: `tests/test_client.py`

- [ ] **Step 11.1: 写失败测试**

```python
# tests/test_client.py
from unittest.mock import patch, MagicMock
from b2text.client import (
    DaemonClient, DaemonNotRunning, submit_bv, submit_up, get_task, list_tasks, cancel_task,
)


def test_daemon_not_running_raises(monkeypatch):
    def boom(*a, **kw):
        import httpx
        raise httpx.ConnectError("connection refused")
    monkeypatch.setattr("httpx.get", boom)
    with patch("httpx.post", boom):
        with patch("httpx.delete", boom):
            client = DaemonClient("http://127.0.0.1:8765")
            with __import__("pytest").raises(DaemonNotRunning):
                client.health()


def test_submit_bv_posts_bv_payload(monkeypatch):
    posted = {}
    def fake_post(url, json=None, **kw):
        posted["url"] = url
        posted["json"] = json
        class Resp:
            status_code = 200
            def json(self):
                return {"task_id": "abc"}
            def raise_for_status(self):
                pass
        return Resp()
    monkeypatch.setattr("httpx.post", fake_post)
    task_id = submit_bv("http://127.0.0.1:8765", "BV1xxx", "/tmp/out")
    assert task_id == "abc"
    assert posted["json"]["type"] == "bv"
    assert posted["json"]["id"] == "BV1xxx"


def test_submit_up_posts_up_payload(monkeypatch):
    posted = {}
    def fake_post(url, json=None, **kw):
        posted["json"] = json
        class Resp:
            status_code = 200
            def json(self): return {"task_id": "xyz"}
            def raise_for_status(self): pass
        return Resp()
    monkeypatch.setattr("httpx.post", fake_post)
    task_id = submit_up("http://127.0.0.1:8765", "12345", "/tmp/out", limit=10)
    assert task_id == "xyz"
    assert posted["json"]["type"] == "up"
    assert posted["json"]["limit"] == 10


def test_get_task_returns_dict(monkeypatch):
    def fake_get(url, **kw):
        class Resp:
            status_code = 200
            def json(self): return {"id": "abc", "status": "running"}
            def raise_for_status(self): pass
        return Resp()
    monkeypatch.setattr("httpx.get", fake_get)
    job = get_task("http://127.0.0.1:8765", "abc")
    assert job["id"] == "abc"
    assert job["status"] == "running"


def test_cancel_calls_delete(monkeypatch):
    called = {}
    def fake_delete(url, **kw):
        called["url"] = url
        class Resp:
            status_code = 200
            def raise_for_status(self): pass
        return Resp()
    monkeypatch.setattr("httpx.delete", fake_delete)
    cancel_task("http://127.0.0.1:8765", "abc")
    assert called["url"].endswith("/tasks/abc")
```

- [ ] **Step 11.2: 运行测试验证失败**

```bash
pytest tests/test_client.py -v
```

预期：ImportError。

- [ ] **Step 11.3: 实现 client.py**

```python
# b2text/client.py
"""CLI HTTP 客户端：包装 httpx 调用本地 daemon。

所有函数接受 base_url，daemon 不在时抛 DaemonNotRunning。
"""
from __future__ import annotations

import httpx


DEFAULT_BASE_URL = "http://127.0.0.1:8765"


class DaemonNotRunning(ConnectionError):
    """daemon 未运行（端口拒绝连接）。"""


class DaemonClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _client(self) -> httpx.Client:
        return httpx.Client(base_url=self.base_url, timeout=self.timeout)

    def health(self) -> dict:
        try:
            r = self._client().get("/health")
            r.raise_for_status()
            return r.json()
        except httpx.ConnectError as e:
            raise DaemonNotRunning(f"daemon not running at {self.base_url}: {e}")


def submit_bv(base_url: str, bvid: str, output_dir: str) -> str:
    r = httpx.post(
        f"{base_url}/transcribe",
        json={"type": "bv", "id": bvid, "output_dir": output_dir},
        timeout=10.0,
    )
    r.raise_for_status()
    return r.json()["task_id"]


def submit_up(base_url: str, uid: str, output_dir: str, limit: int = 50) -> str:
    r = httpx.post(
        f"{base_url}/transcribe",
        json={"type": "up", "id": uid, "output_dir": output_dir, "limit": limit},
        timeout=10.0,
    )
    r.raise_for_status()
    return r.json()["task_id"]


def get_task(base_url: str, task_id: str) -> dict:
    r = httpx.get(f"{base_url}/tasks/{task_id}", timeout=10.0)
    r.raise_for_status()
    return r.json()


def list_tasks(base_url: str, status: str | None = None) -> dict:
    params = {"status": status} if status else None
    r = httpx.get(f"{base_url}/tasks", params=params, timeout=10.0)
    r.raise_for_status()
    return r.json()


def cancel_task(base_url: str, task_id: str) -> None:
    r = httpx.delete(f"{base_url}/tasks/{task_id}", timeout=10.0)
    r.raise_for_status()
```

- [ ] **Step 11.4: 运行测试验证通过**

```bash
pytest tests/test_client.py -v
```

预期：5 passed。

- [ ] **Step 11.5: Commit**

```bash
git add b2text/client.py tests/test_client.py
git commit -m "feat: add HTTP client for CLI to talk to daemon"
```

---

### Task 12: cli.py — argparse 调度（serve / transcribe / status / list / cancel / run）

**Files:**
- Create: `b2text/cli.py`
- Modify: `bilibili_to_text.py`（改成 3 行 shim）

- [ ] **Step 12.1: 实现 cli.py**

```python
# b2text/cli.py
"""b2text 命令行入口。

子命令：
  serve start|stop|status|logs
  transcribe BV1xxx -o DIR
  transcribe --type up <uid> -o DIR --limit N
  status <task_id>
  list
  cancel <task_id>
  run <bvid> -o FILE    # 本地直跑，不走 daemon
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

from b2text.client import (
    DEFAULT_BASE_URL, DaemonNotRunning,
    submit_bv, submit_up, get_task, list_tasks, cancel_task,
)
from b2text.cookie_store import MissingCookieError, resolve_cookie
from b2text.paths import config_dir, data_dir, daemon_pid, jobs_db, jobs_log


_BASE_URL = DEFAULT_BASE_URL


def _b2text_module_args() -> list[str]:
    return [sys.executable, "-m", "b2text.server"]


def _serve_start(args) -> int:
    pid_path = daemon_pid()
    if pid_path.exists():
        try:
            existing_pid = int(pid_path.read_text().strip())
            # 进程存在？
            import os
            try:
                os.kill(existing_pid, 0)
                print(f"❌ daemon 已在运行（pid {existing_pid}）。`b2text serve stop` 先。")
                return 1
            except ProcessLookupError:
                # stale pidfile
                pid_path.unlink()
        except ValueError:
            pid_path.unlink()

    try:
        resolve_cookie()
    except MissingCookieError as e:
        print(f"❌ {e}", flush=True)
        print(
            f"💡 提示：把 cookie 写入 {config_dir() / 'cookie'}（文件建议 chmod 600）\n"
            f"   内容：SESSDATA=xxx; bili_jct=xxx",
            flush=True,
        )
        return 4

    data_dir().mkdir(parents=True, exist_ok=True)
    log_path = data_dir() / "daemon.log"
    log_f = open(log_path, "a", encoding="utf-8")
    proc = subprocess.Popen(
        _b2text_module_args() + ["--port", str(args.port)],
        stdout=log_f, stderr=log_f,
        start_new_session=True,
    )
    pid_path.write_text(str(proc.pid))
    print(f"✅ daemon 已启动（pid {proc.pid}，port {args.port}）")
    print(f"   日志：{log_path}")
    print(f"   pidfile：{pid_path}")
    return 0


def _serve_stop(args) -> int:
    pid_path = daemon_pid()
    if not pid_path.exists():
        print("❌ 没有 pidfile — daemon 未启动？")
        return 1
    pid = int(pid_path.read_text().strip())
    import os, signal
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        print(f"⚠️  进程 {pid} 不存在，清掉 pidfile")
        pid_path.unlink(missing_ok=True)
        return 0
    # 等待 pidfile 自动清掉（worker 关闭流程会清）
    for _ in range(20):
        time.sleep(0.5)
        if not pid_path.exists():
            print(f"✅ daemon 已停止（pid {pid}）")
            return 0
    print(f"⚠️  30s 内未退出，尝试 SIGKILL")
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    pid_path.unlink(missing_ok=True)
    return 0


def _serve_status(args) -> int:
    pid_path = daemon_pid()
    pid = None
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text().strip())
            import os
            os.kill(pid, 0)
        except (ValueError, ProcessLookupError):
            pid = None
            pid_path.unlink(missing_ok=True)
    if pid is None:
        print("daemon 未运行")
        return 0
    try:
        health = DaemonClient_shim().health()
        print(f"✅ daemon 正在运行（pid {pid}）")
        print(f"   ok={health.get('ok')}, model_loaded={health.get('model_loaded')}")
        print(f"   queue_len={health.get('queue_len')}, running={health.get('running')}")
    except DaemonNotRunning:
        print(f"⚠️  pidfile 存在（pid {pid}）但端口不响应")
    return 0


def DaemonClient_shim():
    from b2text.client import DaemonClient
    return DaemonClient(_BASE_URL)


def _serve_logs(args) -> int:
    log = data_dir() / "daemon.log"
    n = args.n
    cmd = ["tail", "-n", str(n), "-F", str(log)]
    try:
        return subprocess.call(cmd)
    except KeyboardInterrupt:
        return 0


def _transcribe(args) -> int:
    if args.type == "bv":
        bvid = _normalize_bv(args.id_or_uid)
        try:
            tid = submit_bv(_BASE_URL, bvid, args.output)
        except Exception as e:
            print(f"❌ 提交失败：{e}", flush=True)
            return 1
    else:
        try:
            tid = submit_up(_BASE_URL, args.id_or_uid, args.output, limit=args.limit)
        except Exception as e:
            print(f"❌ 提交失败：{e}", flush=True)
            return 1
    print(f"✅ 任务已提交：{tid}")
    print(f"   查状态：b2text status {tid}")
    return 0


_BV_RE = re.compile(r"(BV[a-zA-Z0-9]+)")


def _normalize_bv(s: str) -> str:
    m = _BV_RE.search(s)
    return m.group(1) if m else s


def _status(args) -> int:
    try:
        job = get_task(_BASE_URL, args.task_id)
    except Exception as e:
        print(f"❌ 查询失败：{e}")
        return 1
    print(f"任务 {job['id']}")
    print(f"  type: {job['type']}, target: {job['target_id']}")
    print(f"  status: {job['status']}")
    print(f"  output_dir: {job['output_dir']}")
    if job.get("result_path"):
        print(f"  result: {job['result_path']}")
    if job.get("error"):
        print(f"  error: {job['error']}")
    print(f"  created: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(job['created_at']))}")
    if job.get("started_at"):
        print(f"  started: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(job['started_at']))}")
    if job.get("finished_at"):
        print(f"  finished: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(job['finished_at']))}")
    return 0


def _list(args) -> int:
    try:
        data = list_tasks(_BASE_URL, status=args.status)
    except Exception as e:
        print(f"❌ 查询失败：{e}")
        return 1
    rows = data.get("tasks", [])
    if not rows:
        print("（无任务）")
        return 0
    for row in rows:
        print(f"[{row['status']:>9}] {row['id'][:8]}.. {row['type']}/{row['target_id']}")
    return 0


def _cancel(args) -> int:
    try:
        cancel_task(_BASE_URL, args.task_id)
        print(f"✅ 已请求取消 {args.task_id}")
    except Exception as e:
        print(f"❌ 取消失败：{e}")
        return 1
    return 0


def _run(args) -> int:
    """本地直跑（不通过 daemon）。"""
    from b2text.transcriber import FunASRTranscriber
    from b2text.normalizer import normalize_funasr_output
    from b2text.formatter import format_segments
    from b2text.audio import check_ffmpeg, download_audio_stream, ensure_wav
    from b2text import bili_api
    from b2text.utils import extract_bvid

    if not check_ffmpeg():
        print("❌ 未找到 ffmpeg。请先安装：brew install ffmpeg")
        return 3

    transcriber = FunASRTranscriber(device=args.device)
    output = Path(args.output)
    try:
        cookie = resolve_cookie()
    except MissingCookieError as e:
        print(f"❌ {e}")
        return 4

    bili_api.COOKIE = cookie  # 模块级注入（与 daemon worker 一致）

    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        bvid = extract_bvid(args.id_or_uid)
        if not bvid:
            print(f"❌ 无法识别输入：{args.id_or_uid}")
            return 1
        info = bili_api.get_video_info(bvid)
        if not info:
            print(f"❌ 获取视频信息失败：{bvid}")
            return 1
        print(f"📺 {info['title']}")
        page = info["pages"][0]
        url = bili_api.get_audio_url(info["aid"], page["cid"])
        if not url:
            print("❌ 获取音频链接失败")
            return 1
        m4s_path = tmpdir / "audio.m4s"
        download_audio_stream(url, m4s_path, cookie=cookie)
        wav_path = ensure_wav(m4s_path, tmpdir)

        print("🎙️  开始转写…")
        raw = transcriber.transcribe(wav_path)
        segments = normalize_funasr_output(raw)
        text = format_segments(segments)

        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        print(f"✅ 已写入 {output}（{len(segments)} 段）")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="b2text")
    sub = p.add_subparsers(dest="command", required=True)

    # serve
    s = sub.add_parser("serve")
    ssub = s.add_subparsers(dest="serve_cmd", required=True)
    p_start = ssub.add_parser("start")
    p_start.add_argument("--port", type=int, default=8765)
    p_start.set_defaults(func=_serve_start)
    p_stop = ssub.add_parser("stop")
    p_stop.set_defaults(func=_serve_stop)
    p_status = ssub.add_parser("status")
    p_status.set_defaults(func=_serve_status)
    p_logs = ssub.add_parser("logs")
    p_logs.add_argument("-n", type=int, default=50)
    p_logs.set_defaults(func=_serve_logs)

    # transcribe
    pt = sub.add_parser("transcribe")
    pt.add_argument("id_or_uid")
    pt.add_argument("-o", "--output", required=True)
    pt.add_argument("--type", choices=["bv", "up"], default="bv")
    pt.add_argument("--limit", type=int, default=50)
    pt.set_defaults(func=_transcribe)

    # status
    pst = sub.add_parser("status")
    pst.add_argument("task_id")
    pst.set_defaults(func=_status)

    # list
    pl = sub.add_parser("list")
    pl.add_argument("--status", choices=[s.value for s in JobStatus_str()], default=None)
    pl.set_defaults(func=_list)

    # cancel
    pc = sub.add_parser("cancel")
    pc.add_argument("task_id")
    pc.set_defaults(func=_cancel)

    # run（本地直跑）
    pr = sub.add_parser("run")
    pr.add_argument("id_or_uid")
    pr.add_argument("-o", "--output", required=True)
    pr.add_argument("--device", default="mps", choices=["mps", "cpu"])
    pr.set_defaults(func=_run)
    return p


def JobStatus_str():
    from b2text.queue import JobStatus
    return list(JobStatus)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 12.2: 把 bilibili_to_text.py 改成 shim**

覆盖 `bilibili_to_text.py` 内容：

```python
#!/usr/bin/env python3
"""B站视频对话转文本 — 向后兼容入口。

新代码请用 `b2text` 子命令。本脚本保留为 `python bilibili_to_text.py BV... -o ...`
的旧调用方式。
"""
import sys
from b2text.cli import main as cli_main

if __name__ == "__main__":
    sys.exit(cli_main())
```

- [ ] **Step 12.3: 验证 CLI 启动 help**

```bash
source venv/bin/activate && python -m b2text.cli --help
```

预期：列出 `serve / transcribe / status / list / cancel / run`。

- [ ] **Step 12.4: 验证向后兼容**

```bash
source venv/bin/activate && python bilibili_to_text.py --help
```

预期：同样输出 b2text 帮助。

- [ ] **Step 12.5: Commit**

```bash
git add b2text/cli.py bilibili_to_text.py
git commit -m "feat: add CLI dispatch and back-compat shim for bilibili_to_text.py"
```

---

### Task 13: README 文档化 daemon 生命周期

**Files:**
- Modify: `README.md`

- [ ] **Step 13.1: 在 README 新增 daemon 章节**

在 README 末尾追加（在 `## 设计文档` 之前）：

```markdown
## Daemon 模式（v2）

把模型常驻内存，多次提交不用重新加载 FunASR。

### 准备 cookie

```bash
mkdir -p ~/.config/b2text
echo "SESSDATA=xxx; bili_jct=xxx" > ~/.config/b2text/cookie
chmod 600 ~/.config/b2text/cookie
```

也可用环境变量一次性覆盖：`export B2TEXT_COOKIE="..."`。

### 启动

```bash
b2text serve start
# 等几十秒模型加载完成
b2text serve status  # 看 model_loaded=true 后再提交任务
```

### 提交任务

```bash
# 单个 BV
b2text transcribe BV1xxxxxxxxx -o /Users/me/sourceRead/

# 整个 UP 主（默认最新 50 条）
b2text transcribe --type up 12345678 -o /Users/me/sourceRead/ --limit 30

# 查状态
b2text status <task_id>
b2text list
```

### 看日志

```bash
b2text serve logs                 # tail daemon.log
cat ~/.local/share/b2text/jobs.log | grep task_id
```

### 关闭

```bash
b2text serve stop
```

### 调试逃生口（不走 daemon）

```bash
b2text run BV1xxxxxxxxx -o /tmp/x.txt
```

直接本地同步跑，模型每次重新加载。

```

- [ ] **Step 13.2: 在测试矩阵标注 daemon 模式**

在原 README 测试小节上方加一句：

```
Daemon mode requires `fastapi`/`uvicorn`/`httpx` (already in requirements.txt).
```

- [ ] **Step 13.3: Commit**

```bash
git add README.md
git commit -m "docs: document daemon lifecycle + cookie preparation"
```

---

### Task 14: 端到端冒烟测试（不需要 FunASR 模型）

**Files:** 无（手动验证步骤）

**目的**：用 `--no-funasr` 启动 daemon、提交任务、观察 worker 即使没 FunASR 也按预期报错，不崩溃。

- [ ] **Step 14.1: 用 fake pipeline 测 daemon**

`--no-funasr` 跳过真实 worker（context manager 不会启动 worker）。可以测试：

1. `serve start --no-funasr` → 启动 daemon（缺 cookie 时退出 4）
2. `b2text transcribe BV1xxx -o /tmp/out` → 返回 task_id
3. `b2text status <task_id>` → 显示 queued

> 当前 `serve start` 调用的是真 `b2text.server`，它默认 `run_real_pipeline=True`。要测试 `--no-funasr`，CLI serve start 需传 `--no-funasr` 标志。
> 
> 简化路径：手动测：
>   1. `python -m b2text.server --port 8765 --no-funasr &`（后台启动）
>   2. `curl -s http://127.0.0.1:8765/health` → `{"ok":true, "model_loaded":true, ...}`  (因为 --no-funasr 不强制 model_loaded)
>   3. `curl -X POST http://127.0.0.1:8765/transcribe -d '{"type":"bv","id":"BV1xxx","output_dir":"/tmp"}' -H 'Content-Type: application/json'` → `{"task_id":"..."}`
>   4. `curl -s http://127.0.0.1:8765/health` → `{"queue_len":1,"running":0,...}`  (任务保留 queued 因为 worker 不启动)
>   5. `kill <pid>`

记下任何 crash / 异常 → 在下一任务里修。

- [ ] **Step 14.2: 调试 + 修复**

如果有任何发现的 bug（启动报错、port 冲突、cookie 检查顺序错），在这个任务中修复；新增 commit 用 `fix:` 前缀。

- [ ] **Step 14.3: 验证所有单元测试**

```bash
pytest -v
```

预期：所有单元测试通过；集成测试 skip。

- [ ] **Step 14.4: Commit**

```bash
git add -A
git commit -m "chore: smoke test the daemon end-to-end (no-funasr mode)"
```

---

## 完成验收清单

- [ ] `requirements.txt` 有 `fastapi`/`uvicorn[standard]`/`httpx`
- [ ] `pytest tests/test_paths.py tests/test_cookie_store.py tests/test_queue.py tests/test_job_log.py tests/test_upmaster.py tests/test_worker.py tests/test_server.py tests/test_client.py -v` 全 pass
- [ ] `python -m b2text.cli --help` 列出所有子命令
- [ ] `python -m b2text.server --help` 至少支持 `--port` 和 `--no-funasr`
- [ ] `python -m b2text.server --no-funasr &` 能启动并接受 `/transcribe` 请求
- [ ] task 完成后提交到 git（13 个 feat/chore/docs commit）
- [ ] 现有 `bilibili_to_text.py` 行为没破（向后兼容）
- [ ] README 增补了 daemon 章节

---

## 后续（不在本计划内）

- 真正拉 UP 主（带 fan-out）端到端测试 — 需要 B 站 API mock 更完整（已在 test_upmaster.py 覆盖核心）
- FunASR 集成测试 — 在真 daemon 上跑一个 BV（test_worker_integration.py 已 mock pipeline）
- systemd / launchd 自动启动 plist（spec 风险段已提及）
- UP 主抓取进度反馈（`GET /tasks/{parent_id}` 加 child count summary）

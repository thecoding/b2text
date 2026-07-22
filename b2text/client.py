"""CLI HTTP client: wraps httpx calls to the local daemon.

All functions take base_url; raise DaemonNotRunning when the daemon is down.
"""
from __future__ import annotations

import httpx


DEFAULT_BASE_URL = "http://127.0.0.1:8765"


class DaemonNotRunning(ConnectionError):
    """daemon not running (connection refused)."""


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


def cleanup_tasks(
    base_url: str,
    *,
    status: str | None = None,
    older_than_seconds: float | None = None,
    all: bool = False,
    cascade: bool = True,
) -> int:
    """DELETE /tasks，返回删除条数。"""
    params: dict[str, str | float] = {"cascade": "true" if cascade else "false"}
    if status is not None:
        params["status"] = status
    if older_than_seconds is not None:
        params["older_than_seconds"] = older_than_seconds
    if all:
        params["all"] = "true"
    r = httpx.delete(f"{base_url}/tasks", params=params, timeout=10.0)
    r.raise_for_status()
    return r.json()["deleted"]

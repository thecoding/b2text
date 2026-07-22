"""Rate limiter for outbound B站 API calls.

Single daemon process hits B站 sequentially across many jobs. Without
pacing, even legitimate users hit code=-799 ("请求过于频繁"). This module
provides a thread-safe token bucket shared across all B站 API call sites.
"""
from __future__ import annotations

import threading
import time


class TokenBucket:
    """Token bucket: refills at `rate` tokens/sec, capped at `capacity`.

    acquire() blocks until a token is available. Safe for concurrent use
    across threads (FunASR + FastAPI threadpool + worker executor).

    Rate is in tokens/second. Capacity is the burst size — first N calls
    in quick succession don't wait.
    """

    def __init__(self, rate: float, capacity: int):
        if rate <= 0:
            raise ValueError(f"rate must be > 0, got {rate}")
        if capacity <= 0:
            raise ValueError(f"capacity must be > 0, got {capacity}")
        self.rate = rate
        self.capacity = capacity
        self.tokens = float(capacity)
        self.last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Block until a token is available, then consume one."""
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self.last
                self.last = now
                self.tokens = min(
                    self.capacity, self.tokens + elapsed * self.rate
                )
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
                # Compute wait *inside* the lock so two waiters don't
                # both race for the same token. Short sleep then re-check.
                wait = (1.0 - self.tokens) / self.rate
            time.sleep(wait)


# Shared bucket for all B站 API calls.
# 1 req/sec steady-state; capacity 3 lets a fresh daemon burst three
# requests (e.g., get_video_info + get_audio_url + upmaster fetch)
# without immediately throttling itself.
_BILI_BUCKET = TokenBucket(rate=1.0, capacity=3)
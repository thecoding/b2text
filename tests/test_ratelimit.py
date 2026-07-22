"""tests/test_ratelimit.py — Token bucket rate limiter."""
import time
from b2text.ratelimit import TokenBucket


def test_capacity_burst_returns_immediately():
    """First `capacity` acquires don't wait."""
    bucket = TokenBucket(rate=1.0, capacity=3)
    t0 = time.monotonic()
    for _ in range(3):
        bucket.acquire()
    elapsed = time.monotonic() - t0
    assert elapsed < 0.05, f"capacity burst should be instant, took {elapsed:.3f}s"


def test_acquire_blocks_until_token_available():
    """After capacity exhausted, acquire waits for next token (~1/rate sec)."""
    bucket = TokenBucket(rate=10.0, capacity=1)  # 10 tokens/sec = 0.1s/token
    bucket.acquire()  # consume the one initial token
    t0 = time.monotonic()
    bucket.acquire()  # must wait ~0.1s for next token
    elapsed = time.monotonic() - t0
    assert 0.08 <= elapsed <= 0.30, f"expected ~0.1s wait, got {elapsed:.3f}s"


def test_rate_controls_throughput():
    """N acquires at rate=R should take at least (N-1)/R seconds after burst."""
    bucket = TokenBucket(rate=5.0, capacity=1)
    bucket.acquire()  # burn initial token
    t0 = time.monotonic()
    for _ in range(3):
        bucket.acquire()
    elapsed = time.monotonic() - t0
    # 3 more tokens at 5/s = 0.6s total minimum
    assert elapsed >= 0.5, f"expected >=0.6s for 3 tokens at 5/s, got {elapsed:.3f}s"


def test_refill_caps_at_capacity():
    """After idle period, only `capacity` tokens are available — no unbounded refill."""
    bucket = TokenBucket(rate=1.0, capacity=2)
    time.sleep(0.5)  # would refill 0.5 tokens at 1/s
    t0 = time.monotonic()
    bucket.acquire()
    bucket.acquire()
    elapsed = time.monotonic() - t0
    # Should not have waited: only 2 tokens, both consumed fast
    assert elapsed < 0.05
    # Third acquire should wait
    t0 = time.monotonic()
    bucket.acquire()
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.5, f"third acquire after idle should wait ~1s, got {elapsed:.3f}s"


def test_thread_safety():
    """Concurrent acquires from multiple threads shouldn't violate rate."""
    import threading
    bucket = TokenBucket(rate=20.0, capacity=2)
    timestamps: list[float] = []
    lock = threading.Lock()

    def worker():
        bucket.acquire()
        with lock:
            timestamps.append(time.monotonic())

    threads = [threading.Thread(target=worker) for _ in range(8)]
    t0 = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.monotonic() - t0

    # 8 acquires at 20/s with capacity 2: 2 instant + 6 spaced ~0.05s = ~0.3s minimum
    assert elapsed >= 0.25, f"expected concurrent 8 acquires at 20/s to take >=0.3s, got {elapsed:.3f}s"
    # Sanity: timestamps should be monotonically non-decreasing
    assert timestamps == sorted(timestamps)
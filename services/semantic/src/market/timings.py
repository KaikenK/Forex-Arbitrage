"""Rolling per-stage timings reported by /metrics (engine update C38).

The service had no timing instrumentation, so latency regressions (a blocked event loop, a slow
tracker) were only visible as backend timeouts. Each stage keeps its last 500 samples.
"""
from __future__ import annotations

import asyncio
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from typing import Iterator

WINDOW = 500
LOOP_PROBE_SECONDS = 0.5

_samples: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=WINDOW))
_lock = threading.Lock()


def record(stage: str, milliseconds: float) -> None:
    with _lock:
        _samples[stage].append(milliseconds)


@contextmanager
def timed(stage: str) -> Iterator[None]:
    start = time.perf_counter()
    try:
        yield
    finally:
        record(stage, (time.perf_counter() - start) * 1000.0)


def summary() -> dict[str, dict[str, float]]:
    with _lock:
        snapshot = {stage: sorted(values) for stage, values in _samples.items() if values}
    return {
        stage: {"count": len(values), "mean": round(sum(values) / len(values), 2),
                "p50": round(values[len(values) // 2], 2),
                "p95": round(values[min(len(values) - 1, int(0.95 * len(values)))], 2),
                "max": round(values[-1], 2)}
        for stage, values in snapshot.items()
    }


async def watch_event_loop(interval: float = LOOP_PROBE_SECONDS) -> None:
    """Records how late a short sleep wakes up: how long the event loop was blocked."""
    while True:
        start = time.perf_counter()
        await asyncio.sleep(interval)
        record("event_loop_lag", max(0.0, (time.perf_counter() - start - interval) * 1000.0))

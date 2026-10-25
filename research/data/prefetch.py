"""Parallel, resumable, laptop-safe prefetch of Dukascopy hour files.

The sequential fetch in `event_windows.fetch_plan` downloads one pair-hour at a time at
10–25 s each — 31 to 78 hours for the full study. Most of that time is spent waiting on the
network, not computing, so a small pool of threads overlaps the waits.

Design constraints, in priority order:

1. **Correctness of the cache.** Writes are atomic (see `ensure_hour_cached`); a killed run
   never leaves a truncated file. Re-running skips everything already cached.
2. **Politeness.** Dukascopy is a free public feed. Workers are capped, failures back off
   exponentially with jitter, and a 429 from any worker pauses *every* worker, so a
   throttled server is not hit by several threads at once.
3. **Laptop safety.** The work is I/O-bound. Threads only download and write bytes; nothing
   is decoded here, so CPU and memory stay low while the machine remains usable.
"""
from __future__ import annotations

import random
import threading
import time
import urllib.error
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .event_windows import FetchPlan
from .intraday_pipeline import _cache_path, ensure_hour_cached, is_hour_cached

DEFAULT_WORKERS = 4
# Hard cap regardless of what is requested: more threads mostly earn more 429s from a free
# feed, and the laptop gains nothing from them.
MAX_WORKERS = 8
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_BASE_BACKOFF_SECONDS = 2.0
MAX_BACKOFF_SECONDS = 60.0
MAX_RECORDED_FAILURES = 50


class PermanentFetchError(Exception):
    """A failure that retrying will not fix (e.g. HTTP 404 for a pair-hour)."""


@dataclass
class PrefetchReport:
    total: int = 0
    already_cached: int = 0
    downloaded: int = 0
    empty: int = 0
    failed: int = 0
    throttled: int = 0
    retries: int = 0
    elapsed_seconds: float = 0.0
    interrupted: bool = False
    failures: list[dict[str, str]] = field(default_factory=list)

    @property
    def attempted(self) -> int:
        return self.total - self.already_cached

    @property
    def hours_per_second(self) -> float:
        done = self.downloaded + self.failed
        return done / self.elapsed_seconds if self.elapsed_seconds > 0 else 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "already_cached": self.already_cached,
            "downloaded": self.downloaded,
            "empty": self.empty,
            "failed": self.failed,
            "throttled": self.throttled,
            "retries": self.retries,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "hours_per_second": round(self.hours_per_second, 3),
            "interrupted": self.interrupted,
            "failures": self.failures,
        }


ProgressFn = Callable[[PrefetchReport, int, float | None], None]


class _ThrottleGate:
    """Shared pause: when one worker is throttled, every worker waits it out."""

    def __init__(self, clock: Callable[[], float], sleep: Callable[[float], None]) -> None:
        self._clock = clock
        self._sleep = sleep
        self._resume_at = 0.0
        self._lock = threading.Lock()

    def hold(self, seconds: float) -> None:
        with self._lock:
            self._resume_at = max(self._resume_at, self._clock() + seconds)

    def wait(self) -> None:
        with self._lock:
            remaining = self._resume_at - self._clock()
        if remaining > 0:
            self._sleep(remaining)


def _classify(exc: BaseException) -> str:
    """'throttle', 'retry' or 'permanent'.

    Throttling is not only HTTP 429. The 2026-10-24 pilot against Dukascopy saw no 429s at
    all: the server shed load with 503s and by resetting connections. Those must pause
    every worker, or four threads keep hammering a server that is already refusing them.
    """
    if isinstance(exc, PermanentFetchError):
        return "permanent"
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (429, 503):
            return "throttle"
        if exc.code >= 500:
            return "retry"
        return "permanent"  # 404 and other 4xx will not change on retry
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, ConnectionResetError) or isinstance(exc, ConnectionResetError):
        return "throttle"
    if isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError, OSError)):
        return "retry"
    return "permanent"


def _retry_after_seconds(exc: BaseException) -> float | None:
    if isinstance(exc, urllib.error.HTTPError) and exc.headers is not None:
        value = exc.headers.get("Retry-After")
        if value:
            try:
                return max(0.0, float(value))
            except ValueError:
                return None
    return None


def prefetch_plan(
    plan: FetchPlan,
    cache_dir: Path,
    *,
    workers: int = DEFAULT_WORKERS,
    max_hours: int | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_backoff_seconds: float = DEFAULT_BASE_BACKOFF_SECONDS,
    fetch_raw_fn=None,
    progress: ProgressFn | None = None,
    progress_every: int = 25,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
) -> PrefetchReport:
    """Download every uncached (pair, hour) in `plan` into `cache_dir`, in parallel.

    Resumable by construction: cached hours are skipped before any thread starts.
    `max_hours` caps the number of *uncached* hours attempted, for a pilot run.
    """
    cache_dir = Path(cache_dir)
    workers = max(1, min(int(workers), MAX_WORKERS))
    rng = rng or random.Random()
    report = PrefetchReport()
    lock = threading.Lock()
    gate = _ThrottleGate(clock, sleep)

    tasks: list[tuple[str, datetime]] = []
    for pair, hours in plan.hours_by_pair.items():
        for hour in hours:
            report.total += 1
            if is_hour_cached(pair, hour, cache_dir):
                report.already_cached += 1
            else:
                tasks.append((pair, hour))
    if max_hours is not None:
        skipped = max(0, len(tasks) - max_hours)
        tasks = tasks[:max_hours]
        report.total -= skipped

    def run_one(pair: str, hour: datetime) -> None:
        for attempt in range(max_attempts):
            gate.wait()
            try:
                downloaded = ensure_hour_cached(pair, hour, cache_dir, fetch_raw_fn=fetch_raw_fn)
            except Exception as exc:  # noqa: BLE001 - classified below
                kind = _classify(exc)
                if kind == "permanent" or attempt == max_attempts - 1:
                    with lock:
                        report.failed += 1
                        if len(report.failures) < MAX_RECORDED_FAILURES:
                            report.failures.append(
                                {"pair": pair, "hour": hour.isoformat(), "error": f"{type(exc).__name__}: {exc}"[:200]}
                            )
                    return
                delay = min(MAX_BACKOFF_SECONDS, base_backoff_seconds * (2**attempt))
                delay = rng.uniform(delay / 2, delay)  # jitter, so workers do not retry in step
                if kind == "throttle":
                    delay = max(delay, _retry_after_seconds(exc) or 0.0)
                    gate.hold(delay)
                with lock:
                    report.retries += 1
                    if kind == "throttle":
                        report.throttled += 1
                if kind != "throttle":
                    sleep(delay)
                continue
            with lock:
                if downloaded:
                    report.downloaded += 1
                    if _cache_path(cache_dir, pair, hour).stat().st_size == 0:
                        report.empty += 1
                else:
                    report.already_cached += 1  # another run cached it meanwhile
            return

    started = clock()
    completed = 0
    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="prefetch")
    try:
        pending = {executor.submit(run_one, pair, hour) for pair, hour in tasks}
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            completed += len(done)
            if progress is not None and (completed % progress_every < len(done) or not pending):
                report.elapsed_seconds = clock() - started
                rate = report.hours_per_second
                eta = (len(tasks) - completed) / rate if rate > 0 else None
                progress(report, completed, eta)
    except KeyboardInterrupt:
        # Stop cleanly and hand back what was done. Everything already written is a complete,
        # atomically-renamed file, so the next run resumes from exactly here.
        report.interrupted = True
    finally:
        report.elapsed_seconds = clock() - started
        executor.shutdown(wait=True, cancel_futures=True)
    return report

"""Resumable, fault-tolerant, adaptively paced GDELT backfill.

Design for a multi-hour offline job:
  - One shard per time window, written atomically (temp file + rename), then a `.done`
    sentinel. A crash or kill loses at most the in-flight windows; re-running resumes.
  - Pacing adapts to the server instead of sleeping a fixed 4 s after every window
    (2026-10-24, RESEARCH_TRACKER §6): a throttle response (HTTP 429/503, connection reset,
    or GDELT's plain-text "limit requests" notice) pauses *every* worker via the shared gate
    in `data/prefetch.py`, with exponential backoff and jitter, honouring Retry-After, and
    widens a shared request spacing that then decays back while requests succeed.
  - Defaults are measured, not assumed. A pilot starting at zero spacing with two workers
    drew 70 throttles for 10 windows (0.0074 windows/s): GDELT penalises bursts. The old
    fixed-4 s loop managed a median 57 s per window for the same reason. So the defaults
    are one worker and a spacing that never drops below GDELT's documented limit of one
    request per 5 s. `--min-interval 0 --workers 2` gives burst behaviour for a friendlier
    server. At most two windows are ever in flight.
  - Earlier versions recorded GDELT's rate-limit notice as an empty window. Windows whose
    sentinel says 0 events without `confirmed_empty` are fetched again once.
  - Per-window faults are logged and skipped, never abort the run.
  - `consolidate()` merges all shards into a single deduplicated Parquet event store.

Run:
  python -m research.gdelt_backfill --start 2018-01-01 --end 2026-01-01
  python -m research.gdelt_backfill --start 2018-01-01 --end 2026-01-01 --max-windows 20 --no-consolidate
  python -m research.gdelt_backfill --consolidate            # merge shards -> events.parquet
"""
from __future__ import annotations

import argparse
import json
import os
import random
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from research.data.base import RawEvent
from research.data.gdelt import DEFAULT_QUERY, GdeltRateLimited, fetch_window_once
from research.data.prefetch import _classify, _retry_after_seconds, _ThrottleGate

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
DEFAULT_SHARD_DIR = ARTIFACTS / "gdelt_shards"
DEFAULT_STORE = ARTIFACTS / "events.parquet"
RUN_LOG = ARTIFACTS / "gdelt_runs.jsonl"

FetchFn = Callable[[str, datetime, datetime, int], list[RawEvent]]

MAX_WORKERS = 2                 # GDELT is one free endpoint; more threads only earn throttling
DEFAULT_WORKERS = 1
GDELT_MIN_INTERVAL_SECONDS = 5.0  # documented: "limit requests to one every 5 seconds"
DEFAULT_MAX_ATTEMPTS = 6
DEFAULT_BASE_BACKOFF_SECONDS = 5.0
MAX_BACKOFF_SECONDS = 120.0
MAX_RECORDED_FAILURES = 50


def _window_name(start: datetime) -> str:
    return start.strftime("%Y%m%dT%H%M%S")


def _classify_gdelt(exc: BaseException) -> str:
    if isinstance(exc, GdeltRateLimited):
        return "throttle"
    return _classify(exc)


class _AdaptivePacer:
    """Shared minimum spacing between request starts, learned from the server.

    Starts at `min_interval` (0 = no waiting). Each throttle doubles it (from at least
    `floor` seconds, up to `ceiling`); each success shrinks it by `decay` back toward
    `min_interval`, snapping there once within `floor / 10`. So a server that never
    throttles is never waited on beyond its documented limit, and one that throttles harder
    converges near its real limit instead of being hit repeatedly.
    """

    def __init__(self, clock: Callable[[], float], sleep: Callable[[float], None], *,
                 min_interval: float = 0.0, floor: float = 1.0, ceiling: float = 60.0,
                 decay: float = 0.9) -> None:
        self._clock, self._sleep = clock, sleep
        self._min, self._floor, self._ceiling, self._decay = min_interval, floor, ceiling, decay
        self.interval = min_interval
        self._next_slot = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = self._clock()
            start_at = max(now, self._next_slot)
            self._next_slot = start_at + self.interval
        if start_at > now:
            self._sleep(start_at - now)

    def on_success(self) -> None:
        with self._lock:
            self.interval = max(self._min, self.interval * self._decay)
            if self.interval - self._min < self._floor / 10:
                self.interval = self._min

    def on_throttle(self) -> None:
        with self._lock:
            self.interval = min(self._ceiling, max(self._floor, self._min, self.interval * 2))


@dataclass
class BackfillReport:
    windows: int = 0
    already_done: int = 0
    completed: int = 0
    empty: int = 0
    rechecked_empty: int = 0
    failed: int = 0
    throttled: int = 0
    retries: int = 0
    fetched: int = 0
    elapsed_seconds: float = 0.0
    interrupted: bool = False
    final_interval_seconds: float = 0.0
    failures: list[dict[str, str]] = field(default_factory=list)

    @property
    def windows_per_second(self) -> float:
        done = self.completed + self.failed
        return done / self.elapsed_seconds if self.elapsed_seconds > 0 else 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "windows": self.windows, "already_done": self.already_done,
            "completed": self.completed, "empty": self.empty,
            "rechecked_empty": self.rechecked_empty, "failed": self.failed,
            "throttled": self.throttled, "retries": self.retries, "fetched": self.fetched,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "windows_per_second": round(self.windows_per_second, 4),
            "final_interval_seconds": round(self.final_interval_seconds, 2),
            "interrupted": self.interrupted, "failures": self.failures,
        }


def _needs_fetch(sentinel: Path, retry_empty: bool) -> tuple[bool, bool]:
    """(fetch?, is_recheck). A 0-count sentinel without `confirmed_empty` may be a
    rate-limit notice that an earlier version recorded as an empty window."""
    if not sentinel.exists():
        return True, False
    if not retry_empty:
        return False, False
    try:
        meta = json.loads(sentinel.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True, True
    if meta.get("count", 0) == 0 and not meta.get("confirmed_empty", False):
        return True, True
    return False, False


def _atomic_write_bytes(path: Path, write: Callable[[Path], None]) -> None:
    temp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        write(temp)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _write_window(shard_dir: Path, name: str, events: list[RawEvent]) -> int:
    """Shard first, sentinel last, both atomic: a sentinel never exists without its data.

    `completed_at` (UTC) is stamped after the shard is on disk. The H1/H5 sample freeze
    (`freeze_gdelt_sample.py`, RESEARCH_TRACKER §6 locked protocol) includes a window only if
    it completed before the cutoff, so this is the timestamp that decides membership.
    """
    fetched_at = datetime.now(UTC).isoformat()
    count = 0
    if events:
        frame = pd.DataFrame([event.to_row() for event in events]).drop_duplicates("event_id")
        count = len(frame)
        _atomic_write_bytes(shard_dir / f"{name}.parquet", lambda p: frame.to_parquet(p, index=False))
    meta = {"count": count, "fetched_at": fetched_at, "completed_at": datetime.now(UTC).isoformat(),
            "confirmed_empty": count == 0}
    _atomic_write_bytes(shard_dir / f"{name}.done",
                        lambda p: p.write_text(json.dumps(meta), encoding="utf-8"))
    return count


def backfill(
    start: datetime,
    end: datetime,
    *,
    shard_dir: Path = DEFAULT_SHARD_DIR,
    query: str = DEFAULT_QUERY,
    window_hours: int = 24,
    max_records: int = 250,
    workers: int = DEFAULT_WORKERS,
    min_interval_seconds: float = GDELT_MIN_INTERVAL_SECONDS,
    max_windows: int | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_backoff_seconds: float = DEFAULT_BASE_BACKOFF_SECONDS,
    retry_empty: bool = True,
    fetch_fn: FetchFn = fetch_window_once,
    progress: Callable[[BackfillReport, int, float | None], None] | None = None,
    progress_every: int = 10,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
) -> BackfillReport:
    """Fetch every window in [start, end) that has no `.done` sentinel, ≤ 2 at a time.

    `max_windows` caps the number of windows *attempted*, for a pilot run.
    """
    shard_dir = Path(shard_dir)
    shard_dir.mkdir(parents=True, exist_ok=True)
    workers = max(1, min(int(workers), MAX_WORKERS))
    rng = rng or random.Random()
    report = BackfillReport()
    lock = threading.Lock()
    gate = _ThrottleGate(clock, sleep)
    pacer = _AdaptivePacer(clock, sleep, min_interval=min_interval_seconds)

    tasks: list[tuple[datetime, datetime, bool]] = []
    step = timedelta(hours=window_hours)
    cursor = start
    while cursor < end:
        window_end = min(cursor + step, end)
        report.windows += 1
        fetch, recheck = _needs_fetch(shard_dir / f"{_window_name(cursor)}.done", retry_empty)
        if fetch:
            tasks.append((cursor, window_end, recheck))
        else:
            report.already_done += 1
        cursor = window_end
    if max_windows is not None:
        tasks = tasks[:max_windows]

    def run_one(window_start: datetime, window_end: datetime, recheck: bool) -> None:
        name = _window_name(window_start)
        for attempt in range(max_attempts):
            gate.wait()
            pacer.acquire()
            try:
                events = fetch_fn(query, window_start, window_end, max_records)
            except Exception as exc:  # noqa: BLE001 - classified below
                kind = _classify_gdelt(exc)
                if kind == "permanent" or attempt == max_attempts - 1:
                    with lock:
                        report.failed += 1
                        if len(report.failures) < MAX_RECORDED_FAILURES:
                            report.failures.append(
                                {"window": name, "error": f"{type(exc).__name__}: {exc}"[:200]})
                    return
                delay = min(MAX_BACKOFF_SECONDS, base_backoff_seconds * (2 ** attempt))
                delay = rng.uniform(delay / 2, delay)  # jitter, so the two workers do not retry in step
                if kind == "throttle":
                    delay = max(delay, _retry_after_seconds(exc) or 0.0)
                    gate.hold(delay)
                    pacer.on_throttle()
                with lock:
                    report.retries += 1
                    report.throttled += kind == "throttle"
                if kind != "throttle":
                    sleep(delay)
                continue
            pacer.on_success()
            count = _write_window(shard_dir, name, events)
            with lock:
                report.completed += 1
                report.fetched += count
                report.empty += count == 0
                report.rechecked_empty += recheck
            return

    started = clock()
    completed = 0
    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="gdelt")
    try:
        pending = {executor.submit(run_one, *task) for task in tasks}
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            completed += len(done)
            if progress is not None and (completed % progress_every < len(done) or not pending):
                report.elapsed_seconds = clock() - started
                rate = report.windows_per_second
                eta = (len(tasks) - completed) / rate if rate > 0 else None
                progress(report, completed, eta)
    except KeyboardInterrupt:
        # Every finished window is a complete, atomically renamed shard + sentinel, so the next
        # run resumes from exactly here.
        report.interrupted = True
    finally:
        report.elapsed_seconds = clock() - started
        report.final_interval_seconds = pacer.interval
        executor.shutdown(wait=True, cancel_futures=True)
    return report


def retag(frame: pd.DataFrame) -> pd.DataFrame:
    """Re-apply the current tagger to stored headlines and drop non-FX rows.

    Shards keep the raw headline, so tagger improvements can be replayed over the whole archive
    without re-downloading anything. Tagging precision has a direct effect on the event study
    (a mistagged story contributes an unrelated price path), so this runs on every consolidate.
    """
    from research.data.tagging import tag_categories, tag_currencies

    headlines = frame["headline"].astype(str)
    frame = frame.copy()
    frame["currencies"] = headlines.map(lambda text: ";".join(tag_currencies(text)))
    frame["categories"] = headlines.map(lambda text: ";".join(tag_categories(text)))
    return frame[frame["currencies"] != ""].reset_index(drop=True)


def consolidate(
    shard_dir: Path = DEFAULT_SHARD_DIR,
    store_path: Path = DEFAULT_STORE,
    *,
    apply_retag: bool = True,
) -> int:
    """Merge every shard into one deduplicated event store."""
    shards = sorted(shard_dir.glob("*.parquet"))
    if not shards:
        print("No shards to consolidate.")
        return 0
    frames = [pd.read_parquet(shard) for shard in shards]
    merged = pd.concat(frames, ignore_index=True).drop_duplicates("event_id")
    raw_count = len(merged)
    if apply_retag:
        merged = retag(merged)
        print(f"Retag: {raw_count} -> {len(merged)} events "
              f"({raw_count - len(merged)} non-FX rows dropped)")
    # Rebuild the store from shards (idempotent, reproducible), atomically.
    store_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_bytes(store_path, lambda p: merged.to_parquet(p, index=False))
    print(f"Consolidated {len(shards)} shards -> {len(merged)} unique events at {store_path}")
    return len(merged)


def main() -> None:
    parser = argparse.ArgumentParser(description="Resumable GDELT backfill.")
    parser.add_argument("--start", type=lambda s: datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=UTC))
    parser.add_argument("--end", type=lambda s: datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=UTC))
    parser.add_argument("--shard-dir", type=Path, default=DEFAULT_SHARD_DIR)
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--window-hours", type=int, default=24)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help=f"capped at {MAX_WORKERS}")
    parser.add_argument("--min-interval", type=float, default=GDELT_MIN_INTERVAL_SECONDS,
                        help="floor on the spacing between request starts, seconds")
    parser.add_argument("--max-windows", type=int, default=None, help="attempt at most N windows (pilot)")
    parser.add_argument("--no-retry-empty", action="store_true",
                        help="trust old 0-count sentinels instead of re-checking them")
    parser.add_argument("--no-consolidate", action="store_true", help="skip the merge at the end")
    parser.add_argument("--log", type=Path, default=RUN_LOG)
    parser.add_argument("--consolidate", action="store_true", help="Merge shards then exit.")
    args = parser.parse_args()

    if args.consolidate:
        consolidate(args.shard_dir, args.store)
        return
    if not args.start or not args.end:
        parser.error("--start and --end are required unless --consolidate")

    print(f"Backfill GDELT {args.start.date()} .. {args.end.date()} (window={args.window_hours}h, "
          f"workers={min(args.workers, MAX_WORKERS)}, min interval {args.min_interval:g}s)", flush=True)

    def progress(report: BackfillReport, done: int, eta: float | None) -> None:
        eta_text = f"{eta / 3600:.1f} h" if eta is not None else "?"
        print(f"  {done} windows | {report.fetched} events | {report.windows_per_second:.3f} win/s | "
              f"throttled {report.throttled} | failed {report.failed} | ETA {eta_text}", flush=True)

    started_at = datetime.now(UTC)
    report = backfill(
        args.start, args.end,
        shard_dir=args.shard_dir, window_hours=args.window_hours, workers=args.workers,
        min_interval_seconds=args.min_interval, max_windows=args.max_windows, retry_empty=not args.no_retry_empty, progress=progress,
    )
    summary = report.to_dict()
    print(f"Done: {summary}", flush=True)
    args.log.parent.mkdir(parents=True, exist_ok=True)
    with args.log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "started_at": started_at.isoformat(), "start": args.start.isoformat(),
            "end": args.end.isoformat(), "max_windows": args.max_windows, **summary,
        }) + "\n")
    if not args.no_consolidate and not report.interrupted:
        consolidate(args.shard_dir, args.store)


if __name__ == "__main__":
    main()

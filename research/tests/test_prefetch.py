"""Parallel prefetch: correctness of the cache under concurrency, retries and interruption.

No network: every fetch is injected, and `sleep` is injected so backoff runs instantly.
"""
from __future__ import annotations

import threading
import urllib.error
from collections import Counter
from datetime import UTC, datetime, timedelta
from email.message import Message
from pathlib import Path

import pytest

from research.data import intraday_pipeline
from research.data.event_windows import FetchPlan
from research.data.intraday_pipeline import ensure_hour_cached
from research.data.prefetch import MAX_WORKERS, _ThrottleGate, prefetch_plan
from research.data.tagging import currencies_to_pairs

BASE = datetime(2024, 3, 5, 13, 0, tzinfo=UTC)
PAYLOAD = b"\x00compressed-ticks"


def _plan(pairs=("EUR/USD", "USD/JPY"), hours=6) -> FetchPlan:
    return FetchPlan({pair: [BASE + timedelta(hours=h) for h in range(hours)] for pair in pairs})


def _no_sleep(_seconds: float) -> None:
    return None


class CountingFetch:
    def __init__(self, payload: bytes = PAYLOAD) -> None:
        self.payload = payload
        self.calls: Counter = Counter()
        self._lock = threading.Lock()

    def __call__(self, pair: str, hour: datetime) -> bytes:
        with self._lock:
            self.calls[(pair, hour)] += 1
        return self.payload


def _http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://example", code, "err", headers, None)


def test_every_uncached_hour_is_fetched_exactly_once_in_parallel(tmp_path: Path) -> None:
    fetch = CountingFetch()
    report = prefetch_plan(_plan(hours=24), tmp_path, workers=4, fetch_raw_fn=fetch, sleep=_no_sleep)

    assert report.downloaded == 48
    assert report.failed == 0
    assert len(fetch.calls) == 48
    assert set(fetch.calls.values()) == {1}, "no hour may be downloaded twice"


def test_rerun_resumes_and_downloads_nothing(tmp_path: Path) -> None:
    prefetch_plan(_plan(), tmp_path, fetch_raw_fn=CountingFetch(), sleep=_no_sleep)
    second = CountingFetch()
    report = prefetch_plan(_plan(), tmp_path, fetch_raw_fn=second, sleep=_no_sleep)

    assert report.downloaded == 0
    assert report.already_cached == report.total == 12
    assert not second.calls


def test_partially_cached_plan_fetches_only_the_gap(tmp_path: Path) -> None:
    ensure_hour_cached("EUR/USD", BASE, tmp_path, fetch_raw_fn=CountingFetch())
    fetch = CountingFetch()
    report = prefetch_plan(_plan(), tmp_path, fetch_raw_fn=fetch, sleep=_no_sleep)

    assert report.already_cached == 1
    assert report.downloaded == 11
    assert ("EUR/USD", BASE) not in fetch.calls


def test_failed_write_leaves_no_partial_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A crash between writing and renaming must not leave a file a later run would trust."""
    def broken_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(intraday_pipeline.os, "replace", broken_replace)
    with pytest.raises(OSError):
        ensure_hour_cached("EUR/USD", BASE, tmp_path, fetch_raw_fn=CountingFetch())

    assert not any(tmp_path.rglob("*.bi5")), "no final file after a failed rename"
    assert not any(tmp_path.rglob("*.tmp")), "temp file must be cleaned up"


def test_throttling_is_retried_and_counted(tmp_path: Path) -> None:
    attempts: Counter = Counter()

    def flaky(pair: str, hour: datetime) -> bytes:
        attempts[(pair, hour)] += 1
        if attempts[(pair, hour)] == 1:
            raise _http_error(429, retry_after="3")
        return PAYLOAD

    plan = FetchPlan({"EUR/USD": [BASE]})
    slept: list[float] = []
    # Frozen clock: the gate waits the *remaining* hold, which on a real clock is a hair
    # under Retry-After by the time the worker checks it.
    report = prefetch_plan(plan, tmp_path, fetch_raw_fn=flaky, sleep=slept.append, clock=lambda: 0.0)

    assert report.downloaded == 1
    assert report.throttled == 1
    assert report.retries == 1
    assert slept and slept[0] >= 3.0, "Retry-After must be honoured"


@pytest.mark.parametrize(
    "error",
    [
        _http_error(503),
        urllib.error.URLError(ConnectionResetError(10054, "connection forcibly closed")),
    ],
)
def test_overload_signals_other_than_429_also_pause_every_worker(tmp_path: Path, error: Exception) -> None:
    """The 2026-10-24 pilot: Dukascopy sheds load with 503s and resets, never 429s."""
    attempts: Counter = Counter()

    def overloaded(pair: str, hour: datetime) -> bytes:
        attempts[pair] += 1
        if attempts[pair] == 1:
            raise error
        return PAYLOAD

    report = prefetch_plan(
        FetchPlan({"EUR/USD": [BASE]}), tmp_path, fetch_raw_fn=overloaded, sleep=_no_sleep, clock=lambda: 0.0
    )
    assert report.downloaded == 1
    assert report.throttled == 1


def test_not_found_is_permanent_and_not_cached(tmp_path: Path) -> None:
    calls: Counter = Counter()

    def missing(pair: str, hour: datetime) -> bytes:
        calls[pair] += 1
        raise _http_error(404)

    report = prefetch_plan(FetchPlan({"USD/INR": [BASE]}), tmp_path, fetch_raw_fn=missing, sleep=_no_sleep)

    assert report.failed == 1
    assert calls["USD/INR"] == 1, "404 must not be retried"
    assert report.failures[0]["pair"] == "USD/INR"
    assert not any(tmp_path.rglob("*.bi5"))


def test_transient_errors_give_up_after_max_attempts(tmp_path: Path) -> None:
    calls: Counter = Counter()

    def down(pair: str, hour: datetime) -> bytes:
        calls[pair] += 1
        raise urllib.error.URLError("connection reset")

    report = prefetch_plan(
        FetchPlan({"EUR/USD": [BASE]}), tmp_path, fetch_raw_fn=down, max_attempts=3, sleep=_no_sleep
    )

    assert calls["EUR/USD"] == 3
    assert report.retries == 2
    assert report.failed == 1


def test_empty_hour_is_cached_and_counted(tmp_path: Path) -> None:
    report = prefetch_plan(FetchPlan({"EUR/USD": [BASE]}), tmp_path, fetch_raw_fn=CountingFetch(b""), sleep=_no_sleep)

    assert report.downloaded == 1
    assert report.empty == 1
    files = list(tmp_path.rglob("*.bi5"))
    assert len(files) == 1 and files[0].stat().st_size == 0


def test_max_hours_caps_a_pilot_run(tmp_path: Path) -> None:
    fetch = CountingFetch()
    report = prefetch_plan(_plan(hours=24), tmp_path, max_hours=5, fetch_raw_fn=fetch, sleep=_no_sleep)

    assert report.downloaded == 5
    assert report.total == 5
    assert sum(fetch.calls.values()) == 5


def test_worker_count_is_capped(tmp_path: Path) -> None:
    seen: set[str] = set()

    def record(pair: str, hour: datetime) -> bytes:
        seen.add(threading.current_thread().name)
        return PAYLOAD

    prefetch_plan(_plan(hours=200), tmp_path, workers=100, fetch_raw_fn=record, sleep=_no_sleep)
    assert len(seen) <= MAX_WORKERS


def test_throttle_gate_pauses_every_caller() -> None:
    now = [100.0]
    slept: list[float] = []
    gate = _ThrottleGate(clock=lambda: now[0], sleep=slept.append)

    gate.hold(5.0)
    now[0] = 102.0
    gate.wait()
    assert slept == [pytest.approx(3.0)]

    slept.clear()
    now[0] = 106.0
    gate.wait()
    assert slept == [], "no pause once the hold has expired"


def test_study_policy_covers_the_four_majors_and_drops_aud() -> None:
    assert currencies_to_pairs(["USD"], usd_policy="study") == ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF"]
    assert currencies_to_pairs(["EUR"], usd_policy="study") == ["EUR/USD"]
    assert currencies_to_pairs(["AUD"], usd_policy="study") == []
    assert currencies_to_pairs(["AUD"], usd_policy="all") == ["AUD/USD"], "existing policies unchanged"

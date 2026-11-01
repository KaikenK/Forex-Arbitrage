"""Tests for resumable GDELT backfill (shards + consolidate + adaptive pacing). No network."""
from __future__ import annotations

import json
import threading
import time
import urllib.error
from datetime import UTC, datetime
from email.message import Message

import pytest

from research.data.base import RawEvent
from research.data.gdelt import GdeltMalformedPayload, GdeltRateLimited, decode_payload
from research.gdelt_backfill import GDELT_MIN_INTERVAL_SECONDS, _AdaptivePacer, backfill, consolidate

START = datetime(2024, 1, 1, tzinfo=UTC)


def _fake_event(event_id: str) -> RawEvent:
    return RawEvent(
        event_id=event_id,
        timestamp=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
        headline="ECB rate decision",
        source="gdelt",
        currencies=("EUR",),
        categories=("rate_decision",),
    )


class FakeTime:
    """Deterministic clock: sleeping advances time instead of blocking."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []
        self._lock = threading.Lock()

    def clock(self) -> float:
        with self._lock:
            return self.now

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.sleeps.append(seconds)
            self.now += seconds


def _http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://api.gdeltproject.org", code, "err", headers, None)


def _run(shard_dir, fetch_fn, *, end_day=3, fake=None, **kwargs):
    fake = fake or FakeTime()
    report = backfill(START, datetime(2024, 1, end_day, tzinfo=UTC), shard_dir=shard_dir,
                      window_hours=24, fetch_fn=fetch_fn, clock=fake.clock, sleep=fake.sleep,
                      **kwargs)
    return report, fake


def test_backfill_writes_shards_and_is_resumable(tmp_path):
    shard_dir = tmp_path / "shards"
    calls: list[str] = []

    def fetch_fn(query, start, end, max_records):
        calls.append(start.isoformat())
        return [_fake_event(f"{start.strftime('%Y%m%d')}-a")]

    report, _ = _run(shard_dir, fetch_fn)
    assert report.completed == 2 and len(calls) == 2
    assert len(list(shard_dir.glob("*.done"))) == 2
    assert len(list(shard_dir.glob("*.parquet"))) == 2
    assert not list(shard_dir.glob("*.tmp")), "atomic writes leave no temp files"

    calls.clear()
    report, _ = _run(shard_dir, fetch_fn)
    assert calls == [] and report.already_done == 2


def test_success_path_never_sleeps_without_a_floor(tmp_path):
    report, fake = _run(tmp_path, lambda *a: [_fake_event("x")], end_day=8, min_interval_seconds=0.0)
    assert report.completed == 7
    assert fake.sleeps == [], "no fixed per-window sleep while the server is happy"


def test_default_spacing_respects_gdelt_documented_limit(tmp_path):
    starts: list[float] = []
    fake = FakeTime()

    def fetch_fn(query, start, end, max_records):
        starts.append(fake.clock())
        return []

    _run(tmp_path, fetch_fn, end_day=6, fake=fake)
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert gaps and min(gaps) >= GDELT_MIN_INTERVAL_SECONDS


def test_json_with_control_characters_or_bad_escapes_is_parsed_not_dropped():
    raw_control = '{"articles": [{"url": "u", "title": "Euro\tslides\n", "seendate": "20240101T120000Z"}]}'
    bad_escape = '{"articles": [{"url": "u", "title": "Fed\\\'s Powell", "seendate": "20240101T120000Z"}]}'
    assert len(decode_payload(raw_control)["articles"]) == 1
    assert decode_payload(bad_escape)["articles"][0]["title"] == "Fed\\'s Powell"
    with pytest.raises(GdeltMalformedPayload):
        decode_payload('{"articles": [ {"url": ')


def test_429_backs_off_honours_retry_after_and_resumes(tmp_path):
    attempts: dict[str, int] = {}

    def fetch_fn(query, start, end, max_records):
        key = start.isoformat()
        attempts[key] = attempts.get(key, 0) + 1
        if attempts[key] == 1:
            raise _http_error(429, retry_after="30")
        return [_fake_event(key)]

    report, fake = _run(tmp_path, fetch_fn, end_day=2, workers=1)
    assert report.completed == 1 and report.throttled == 1 and report.failed == 0
    assert max(fake.sleeps) >= 30.0, "Retry-After is a floor on the pause"


def test_gdelt_plain_text_notice_is_throttling_not_an_empty_day(tmp_path):
    with pytest.raises(GdeltRateLimited):
        decode_payload("Please limit requests to one every 5 seconds or contact ...")
    assert decode_payload("") == {"articles": []}

    calls = {"n": 0}

    def fetch_fn(query, start, end, max_records):
        calls["n"] += 1
        if calls["n"] == 1:
            raise GdeltRateLimited("Please limit requests to one every 5 seconds")
        return [_fake_event("real")]

    report, _ = _run(tmp_path, fetch_fn, end_day=2, workers=1)
    assert report.throttled == 1 and report.fetched == 1 and report.empty == 0


def test_permanent_error_is_recorded_and_not_marked_done(tmp_path):
    def fetch_fn(query, start, end, max_records):
        raise _http_error(404)

    report, _ = _run(tmp_path, fetch_fn, end_day=2)
    assert report.failed == 1 and report.retries == 0
    assert not list(tmp_path.glob("*.done")), "a failed window must be retried on the next run"


def test_concurrency_is_capped_at_two(tmp_path):
    active, peak = 0, 0
    lock = threading.Lock()

    def fetch_fn(query, start, end, max_records):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return []

    report = backfill(START, datetime(2024, 1, 11, tzinfo=UTC), shard_dir=tmp_path, window_hours=24,
                      workers=8, min_interval_seconds=0.0, fetch_fn=fetch_fn)
    assert report.completed == 10
    assert peak <= 2


def test_old_zero_count_sentinels_are_rechecked_once(tmp_path):
    """Pre-2026-10-24 runs wrote rate-limit notices as {"count": 0}. Re-fetch those once."""
    (tmp_path / "20240101T000000.done").write_text(json.dumps({"count": 0}), encoding="utf-8")
    calls: list[str] = []

    def fetch_fn(query, start, end, max_records):
        calls.append(start.isoformat())
        return []

    report, _ = _run(tmp_path, fetch_fn, end_day=2)
    assert calls and report.rechecked_empty == 1
    meta = json.loads((tmp_path / "20240101T000000.done").read_text(encoding="utf-8"))
    assert meta["confirmed_empty"] is True

    calls.clear()
    _run(tmp_path, fetch_fn, end_day=2)
    assert calls == [], "a confirmed empty window is not fetched again"


def test_max_windows_caps_a_pilot(tmp_path):
    report, _ = _run(tmp_path, lambda *a: [], end_day=11, max_windows=3)
    assert report.completed == 3 and report.windows == 10


def test_pacer_widens_on_throttle_and_decays_to_zero():
    fake = FakeTime()
    pacer = _AdaptivePacer(fake.clock, fake.sleep, floor=1.0, decay=0.5)
    pacer.on_throttle()
    pacer.on_throttle()
    assert pacer.interval == 2.0
    pacer.acquire()
    pacer.acquire()
    assert fake.sleeps == [2.0], "second request waits one interval"
    for _ in range(10):
        pacer.on_success()
    assert pacer.interval == 0.0


def test_consolidate_dedups_across_shards(tmp_path):
    shard_dir = tmp_path / "shards"
    store = tmp_path / "events.parquet"

    def fetch_fn(query, start, end, max_records):
        if start.day == 1:
            return [_fake_event("dup"), _fake_event("uniq-1")]
        return [_fake_event("dup"), _fake_event("uniq-2")]

    _run(shard_dir, fetch_fn)
    total = consolidate(shard_dir, store)
    assert total == 3  # dup, uniq-1, uniq-2


def test_retag_replays_current_tagger_and_drops_non_fx():
    """Tagger fixes must be replayable over the archive without refetching."""
    import pandas as pd

    from research.gdelt_backfill import retag

    frame = pd.DataFrame([
        {"event_id": "junk", "headline": "Player scoops one million euro in lottery raffle",
         "currencies": "EUR", "categories": ""},
        {"event_id": "france", "headline": "France confirms Koscielny will miss World Cup",
         "currencies": "CHF", "categories": ""},
        {"event_id": "real", "headline": "ECB holds rates as euro steadies on inflation data",
         "currencies": "", "categories": ""},
    ])
    out = retag(frame)

    assert set(out["event_id"]) == {"real"}          # both false positives dropped
    assert "EUR" in out.iloc[0]["currencies"]        # and the real one is (re)tagged
    assert "central_bank" in out.iloc[0]["categories"]


def test_slice_schedule_fetches_only_scheduled_weekday_hours(tmp_path):
    # 2024-01-05 is a Friday, 2024-01-06 a Saturday.
    calls: list[tuple[datetime, datetime]] = []

    def fetch_fn(query, start, end, max_records):
        calls.append((start, end))
        return []

    fake = FakeTime()
    report = backfill(datetime(2024, 1, 5, tzinfo=UTC), datetime(2024, 1, 7, tzinfo=UTC),
                      shard_dir=tmp_path, window_hours=1, slice_hours=[9, 13, 0],
                      weekdays_only=True, fetch_fn=fetch_fn, clock=fake.clock, sleep=fake.sleep)
    # Friday windows ending 09:00, 13:00 and Saturday 00:00 (starts Friday 23:00); none on Saturday.
    assert [(s.hour, e.hour) for s, e in calls] == [(8, 9), (12, 13), (23, 0)]
    assert report.windows == 3 and report.completed == 3
    assert {p.stem for p in tmp_path.glob("*.done")} == {
        "20240105T080000", "20240105T120000", "20240105T230000"}


def test_slice_schedule_resume_refetches_only_missing_slices(tmp_path):
    calls: list[datetime] = []

    def flaky(query, start, end, max_records):
        calls.append(start)
        if start.hour == 12 and len(calls) <= 2:
            raise ValueError("permanent")
        return []

    fake = FakeTime()
    kwargs = dict(shard_dir=tmp_path, window_hours=1, slice_hours=[9, 13], clock=fake.clock,
                  sleep=fake.sleep)
    first = backfill(datetime(2024, 1, 8, tzinfo=UTC), datetime(2024, 1, 9, tzinfo=UTC),
                     fetch_fn=flaky, **kwargs)
    assert first.failed == 1 and first.completed == 1
    calls.clear()
    second = backfill(datetime(2024, 1, 8, tzinfo=UTC), datetime(2024, 1, 9, tzinfo=UTC),
                      fetch_fn=lambda *a: calls.append(a[1]) or [], **kwargs)
    assert second.already_done == 1 and second.completed == 1
    assert [c.hour for c in calls] == [12]


def test_query_rejection_fails_the_window_at_once_instead_of_retrying(tmp_path):
    from research.data.gdelt import GdeltQueryRejected
    calls = []

    def rejected(query, start, end, max_records):
        calls.append(start)
        return decode_payload("Your query was too short or too long.")

    report, _ = _run(tmp_path, rejected, end_day=2)
    assert report.failed == 1 and len(calls) == 1 and report.throttled == 0
    with pytest.raises(GdeltQueryRejected):
        decode_payload("Your query was too short or too long.")

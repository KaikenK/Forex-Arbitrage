"""Tests for the intraday fetch/cache/resample pipeline. No network (fetch injected)."""
from __future__ import annotations

import lzma
import struct
from datetime import UTC, datetime, timedelta

from research.data.intraday_pipeline import (
    build_intraday_series,
    fetch_hour_cached,
    point_for,
    resample_to_bars,
)

_REC = struct.Struct(">IIIff")


def test_point_for_jpy_vs_others():
    assert point_for("USD/JPY") == 1e-3
    assert point_for("EUR/USD") == 1e-5


def test_resample_takes_last_tick_in_bucket():
    base = datetime(2024, 1, 2, 13, 0, tzinfo=UTC)
    ticks = [
        (base + timedelta(seconds=5), 1.1000),
        (base + timedelta(seconds=45), 1.1005),   # later in same minute -> wins
        (base + timedelta(seconds=70), 1.1010),   # next minute
    ]
    bars = resample_to_bars(ticks, interval_minutes=1)
    assert bars == [(base, 1.1005), (base + timedelta(minutes=1), 1.1010)]


def test_resample_5m_buckets():
    base = datetime(2024, 1, 2, 13, 0, tzinfo=UTC)
    ticks = [(base + timedelta(minutes=i), 1.0 + i / 10000) for i in range(12)]
    bars = resample_to_bars(ticks, interval_minutes=5)
    # buckets anchored at :00, :05, :10
    assert [b[0].minute for b in bars] == [0, 5, 10]
    assert abs(bars[0][1] - (1.0 + 4 / 10000)) < 1e-12   # last tick of 13:00-13:04


def _raw_two_ticks() -> bytes:
    """Two ticks: t+10s mid 1.10000, t+70s mid 1.10020."""
    return lzma.compress(
        _REC.pack(10_000, 110010, 109990, 1.0, 1.0)
        + _REC.pack(70_000, 110030, 110010, 1.0, 1.0)
    )


def test_cache_stores_real_bytes_and_reuses_them(tmp_path):
    """Regression: the cache must round-trip actual ticks, not an empty marker."""
    hour = datetime(2024, 1, 2, 13, 0, tzinfo=UTC)
    calls = []

    def fake_raw(pair, when):
        calls.append(when)
        return _raw_two_ticks()

    first = fetch_hour_cached("EUR/USD", hour, tmp_path, fetch_raw_fn=fake_raw)
    assert len(first) == 2
    assert len(calls) == 1

    # Second call is served from cache and must return the SAME ticks, not [].
    second = fetch_hour_cached("EUR/USD", hour, tmp_path, fetch_raw_fn=fake_raw)
    assert len(calls) == 1            # no refetch
    assert second == first            # cache is not poisoned


def test_empty_hour_is_cached_and_not_refetched(tmp_path):
    hour = datetime(2024, 1, 6, 13, 0, tzinfo=UTC)   # Saturday-style empty hour
    calls = []

    def fake_raw(pair, when):
        calls.append(when)
        return b""

    assert fetch_hour_cached("EUR/USD", hour, tmp_path, fetch_raw_fn=fake_raw) == []
    assert fetch_hour_cached("EUR/USD", hour, tmp_path, fetch_raw_fn=fake_raw) == []
    assert len(calls) == 1


def test_build_intraday_series_from_raw_bytes(tmp_path):
    start = datetime(2024, 1, 2, 13, 0, tzinfo=UTC)   # Tuesday
    end = start + timedelta(hours=2)

    series = build_intraday_series(
        "EUR/USD", start, end, tmp_path, fetch_raw_fn=lambda p, w: _raw_two_ticks()
    )
    assert series.pair == "EUR/USD"
    assert len(series.times) == 4          # 2 bars/hour x 2 hours
    assert series.times == sorted(series.times)
    found = series.price_at_or_after(start + timedelta(seconds=30))
    assert found is not None and found[0] == start + timedelta(minutes=1)


def test_build_intraday_series_skips_saturday(tmp_path):
    saturday = datetime(2024, 1, 6, 0, 0, tzinfo=UTC)
    assert saturday.weekday() == 5
    hours = []

    def fake_raw(pair, when):
        hours.append(when)
        return b""

    build_intraday_series("EUR/USD", saturday, saturday + timedelta(hours=3), tmp_path,
                          fetch_raw_fn=fake_raw)
    assert hours == []                     # Saturday never requested


def test_parse_bi5_roundtrip_through_cache(tmp_path):
    hour = datetime(2024, 1, 2, 14, 0, tzinfo=UTC)
    raw = _REC.pack(0, 110010, 109990, 1.0, 1.0) + _REC.pack(30000, 110030, 110010, 1.0, 1.0)
    path = tmp_path / "EURUSD" / f"{hour:%Y%m%d_%H}.bi5"
    path.parent.mkdir(parents=True)
    path.write_bytes(lzma.compress(raw))

    ticks = fetch_hour_cached("EUR/USD", hour, tmp_path)
    assert len(ticks) == 2
    assert abs(ticks[0][1] - 1.10000) < 1e-9

"""Tests for event-windowed fetch planning + timestamp-precision horizon gating."""
from __future__ import annotations

import lzma
import struct
from datetime import UTC, datetime, timedelta

from research.data.event_windows import (
    estimate_savings,
    fetch_plan,
    min_valid_horizon,
    plan_fetch_hours,
    valid_horizons,
)
from research.eventstudy.schema import Event

_REC = struct.Struct(">IIIff")
HORIZONS = [1, 5, 15, 30, 60, 240, 1440]


def _event(event_id, ts, pair="EUR/USD", precision=0.0) -> Event:
    return Event(event_id=event_id, timestamp=ts, pair=pair,
                 timestamp_precision_minutes=precision)


# ---------------------------------------------------------------- precision gating

def test_exact_timestamps_allow_all_horizons():
    assert min_valid_horizon(0.0) == 0.0
    assert valid_horizons(HORIZONS, 0.0) == HORIZONS


def test_gdelt_15min_precision_blocks_subbucket_horizons():
    # 15-min crawl buckets -> only horizons >= 30m are defensible.
    assert min_valid_horizon(15.0) == 30.0
    assert valid_horizons(HORIZONS, 15.0) == [30, 60, 240, 1440]


# ---------------------------------------------------------------- planning

def test_plan_covers_window_around_event():
    ts = datetime(2024, 1, 10, 13, 30, tzinfo=UTC)   # Wednesday
    plan = plan_fetch_hours([_event("e1", ts)], pre_hours=1, post_hours=2)
    hours = plan.hours_by_pair["EUR/USD"]
    assert hours == [
        datetime(2024, 1, 10, 12, tzinfo=UTC),
        datetime(2024, 1, 10, 13, tzinfo=UTC),
        datetime(2024, 1, 10, 14, tzinfo=UTC),
        datetime(2024, 1, 10, 15, tzinfo=UTC),
    ]


def test_overlapping_events_share_hours():
    """Clustered events must not multiply the fetch cost."""
    base = datetime(2024, 1, 10, 13, 0, tzinfo=UTC)
    events = [_event(f"e{i}", base + timedelta(minutes=15 * i)) for i in range(4)]
    plan = plan_fetch_hours(events, pre_hours=1, post_hours=2)
    # All 4 events fall in the same hour -> same 4-hour window, not 16 hours.
    assert plan.total_hours == 4
    savings = estimate_savings(plan, events, pre_hours=1, post_hours=2)
    assert savings["naive_hours"] == 16
    assert savings["reduction"] == 0.75


def test_plan_separates_pairs():
    ts = datetime(2024, 1, 10, 13, 0, tzinfo=UTC)
    plan = plan_fetch_hours(
        [_event("a", ts, "EUR/USD"), _event("b", ts, "USD/JPY")],
        pre_hours=0, post_hours=0,
    )
    assert set(plan.hours_by_pair) == {"EUR/USD", "USD/JPY"}
    assert plan.total_hours == 2


def test_plan_skips_saturday():
    saturday = datetime(2024, 1, 6, 12, 0, tzinfo=UTC)
    assert saturday.weekday() == 5
    plan = plan_fetch_hours([_event("e", saturday)], pre_hours=0, post_hours=0)
    assert plan.total_hours == 0


# ---------------------------------------------------------------- execution

def test_fetch_plan_builds_series_from_cached_bytes(tmp_path):
    raw = lzma.compress(
        _REC.pack(10_000, 110010, 109990, 1.0, 1.0)
        + _REC.pack(70_000, 110030, 110010, 1.0, 1.0)
    )
    ts = datetime(2024, 1, 10, 13, 0, tzinfo=UTC)
    plan = plan_fetch_hours([_event("e1", ts)], pre_hours=0, post_hours=1)

    calls = []

    def fake_raw(pair, hour):
        calls.append((pair, hour))
        return raw

    series_by_pair = fetch_plan(plan, tmp_path, fetch_raw_fn=fake_raw)
    series = series_by_pair["EUR/USD"]
    assert len(calls) == 2                      # exactly the planned hours
    assert len(series.times) == 4               # 2 bars per hour
    assert series.times == sorted(series.times)

    # Re-running is served from cache: no additional fetches.
    fetch_plan(plan, tmp_path, fetch_raw_fn=fake_raw)
    assert len(calls) == 2

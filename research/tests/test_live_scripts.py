"""Arrival-latency study and prospective scoring on synthetic logs (no network, no real data)."""
from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from research import measure_arrival_latency as lat
from research import reproduce as rp
from research import score_live_predictions as live

T0 = datetime(2026, 11, 3, 13, 30, tzinfo=UTC)


def _row(**kw):
    base = {"logged_at": (T0 + timedelta(minutes=3)).isoformat(), "signal_scope": "article", "basis": "release",
            "release_event": "CPI m/m", "release_utc": T0.isoformat(), "published_at": (T0 + timedelta(minutes=2)).isoformat(),
            "publisher_time": (T0 + timedelta(minutes=1)).isoformat(), "feed_url": "https://investinglive.com/feed/news",
            "pair": "EUR/USD", "provider": "rss", "direction": -1.0, "release_direction": -1, "confidence": 0.6,
            "p_direction": 0.7, "market_score": -0.001, "horizon_minutes": 60.0, "event_id": "e1"}
    return {**base, **kw}


def _jsonl(tmp_path, rows):
    path = tmp_path / "live_predictions.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return path


def test_observed_offset_rounds_up_and_needs_ten_releases():
    assert lat.observed_offset([0.5] * 9) is None
    assert lat.observed_offset([0.5] * 10) == 1
    assert lat.observed_offset([1.5] * 10) == 2
    assert lat.observed_offset([4.0] * 10) == 5
    assert lat.observed_offset([40.0] * 10) == 15


def test_latency_rows_dedup_per_source_and_measure_both_clocks(tmp_path):
    rows = [_row(), _row(published_at=(T0 + timedelta(minutes=9)).isoformat()),            # same source, later: dropped
            _row(feed_url="finnhub:news", published_at=(T0 + timedelta(minutes=4)).isoformat()),
            _row(basis="news"), _row(signal_scope="pair_score")]
    frame = lat.load(_jsonl(tmp_path, rows), T0 - timedelta(hours=1), T0 + timedelta(hours=1))
    assert len(frame) == 2
    assert sorted(frame["receipt_latency_min"]) == [2.0, 4.0]
    assert frame["publisher_latency_min"].iloc[0] == pytest.approx(1.0)


def test_prospective_scoring_excludes_synthetic_rows_and_reads_release_basis(tmp_path):
    rows = [_row(provider="synthetic"), _row(event_id="e2"), _row(event_id="e3", basis=None, direction=1.0)]
    path = _jsonl(tmp_path, rows)
    since, until = T0 - timedelta(days=1), T0 + timedelta(days=1)
    v1 = live.load_rows(path, since=since, until=until, basis="v1")
    assert sorted(v1["event_id"]) == ["e2", "e3"]
    rel = live.load_rows(path, since=since, until=until, basis="release")
    assert list(rel["event_id"]) == ["e2"] and rel["prob"].iloc[0] == pytest.approx(0.7)


def test_prospective_scoring_counts_each_headline_once_per_pair(tmp_path):
    at = lambda minutes: (T0 + timedelta(minutes=minutes)).isoformat()
    rows = [_row(event_id="e1", headline="Fed holds rates", published_at=at(40)),        # re-logged after a restart
            _row(event_id="e2", headline="FED holds rates!", published_at=at(2)),         # first receipt: kept
            _row(event_id="e3", headline="Fed holds rates", pair="USD/JPY", published_at=at(5)),
            _row(event_id="e4", headline="ECB cuts", published_at=at(-60 * 30)),          # first seen before --since
            _row(event_id="e5", headline="ECB cuts", published_at=at(10)),
            _row(event_id="e6", headline="ECB cuts", provider="synthetic", published_at=at(-60 * 40))]
    path = _jsonl(tmp_path, rows)
    v1 = live.load_rows(path, since=T0 - timedelta(hours=1), until=T0 + timedelta(days=1), basis="v1")
    assert sorted(v1["event_id"]) == ["e2", "e3"]            # e5 is a copy of e4, which is outside the window
    assert v1.attrs["repeat_rows_dropped"] == 2              # e1, e5; the synthetic e6 never counts
    assert live.headline_key("  FED holds -- rates! ") == "fed holds rates" and live.headline_key(None) == ""


def test_secondary_result_keeps_items_at_most_a_day_old_by_publisher_time(tmp_path):
    at = lambda minutes: (T0 + timedelta(minutes=minutes)).isoformat()
    rows = [_row(event_id="fresh", headline="a", publisher_time=at(-60)),
            _row(event_id="stale", headline="b", publisher_time=at(-60 * 30)),            # backlog re-read
            _row(event_id="ahead", headline="c", publisher_time=at(4)),                   # publisher clock ahead
            _row(event_id="unknown", headline="d", publisher_time=None)]
    frame = live.load_rows(_jsonl(tmp_path, rows), since=T0 - timedelta(hours=1), until=T0 + timedelta(hours=1),
                           basis="v1")
    assert sorted(frame.loc[live.is_fresh(frame), "event_id"]) == ["ahead", "fresh"]
    scored = frame.assign(outcome_bps=[2.0, -1.0, -3.0, 4.0])                            # signal -1 on every row
    summary = live.summarize(scored)
    assert summary["rows_scored"] == 4 and summary["accuracy"] == pytest.approx(0.5)
    assert live.summarize(scored.iloc[0:0])["rows_scored"] == 0


def test_hourly_outcome_uses_bars_opening_at_or_after():
    idx = pd.date_range("2026-11-03 12:00", periods=6, freq="h", tz="UTC")
    bars = pd.Series([1.0, 1.0, 1.001, 1.002, 1.003, 1.004], index=idx)
    # 13:30 + 60 min: entry = 14:00 bar (1.001), exit = first bar at/after 14:30 = 15:00 (1.002).
    assert live.outcome(bars, pd.Timestamp(T0), 60) == pytest.approx(1e4 * math.log(1.002 / 1.001))
    assert pd.isna(live.outcome(bars, pd.Timestamp(T0), 600))


def test_new_reproduce_steps_open_on_their_dates(tmp_path):
    steps = {s.name: s for s in rp.STEPS}
    before = rp.Context(now=rp.LATENCY_SAMPLE_END_UTC - timedelta(minutes=1), artifacts=tmp_path)
    after = rp.Context(now=rp.LIVE_SCORING_FROM_UTC, artifacts=tmp_path)
    assert rp.status(steps["arrival_latency"], before) and rp.status(steps["arrival_latency"], after) is None
    assert rp.status(steps["live_scoring_v1"], before) and rp.status(steps["live_scoring_v1"], after) is None
    assert "arrival_latency.json" in rp.status(steps["arrival_retest"], after)

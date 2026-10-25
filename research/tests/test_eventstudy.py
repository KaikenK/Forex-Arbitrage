"""Deterministic unit tests for the event-study core (no network, no heavy ML)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from research.eventstudy import (
    Event,
    PriceSeries,
    align_event,
    align_events,
    build_atlas,
)


def _minute_series(pair: str, start: datetime, prices: list[float]) -> PriceSeries:
    times = [start + timedelta(minutes=i) for i in range(len(prices))]
    return PriceSeries(pair=pair, times=times, prices=prices)


def _event(ts: datetime, pair: str, categories, event_id="e") -> Event:
    return Event(event_id=event_id, timestamp=ts, pair=pair, categories=tuple(categories))


# ---------------------------------------------------------------- alignment

def test_alignment_entry_is_first_bar_at_or_after_event():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    # flat 1.00 for 5 min, then +1% step to 1.01 and hold.
    series = _minute_series("EUR/USD", start, [1.0, 1.0, 1.0, 1.01, 1.01, 1.01])
    # Event lands between bar 1 and 2 -> entry is bar 2 (time start+2m, price 1.0).
    event = _event(start + timedelta(minutes=1, seconds=30), "EUR/USD", ["macro"])
    obs = align_event(event, series, horizons_minutes=[1, 2, 3])

    assert obs is not None
    assert obs.entry_time == start + timedelta(minutes=2)
    assert obs.entry_price == 1.0
    # +1m -> bar3 = 1.01 -> +1%; +2m -> 1.01; +3m -> 1.01
    assert abs(obs.horizon_returns[1] - 0.01) < 1e-9
    assert abs(obs.horizon_returns[2] - 0.01) < 1e-9


def test_alignment_drops_unmatured_horizon():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    series = _minute_series("EUR/USD", start, [1.0, 1.0, 1.0])  # only 3 bars
    event = _event(start, "EUR/USD", ["macro"])
    obs = align_event(event, series, horizons_minutes=[1, 60])  # 60m never matures
    assert obs is not None
    assert 1 in obs.horizon_returns
    assert 60 not in obs.horizon_returns  # fail-closed, not imputed


def test_alignment_fail_closed_when_no_price_after_event():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    series = _minute_series("EUR/USD", start, [1.0, 1.0])
    event = _event(start + timedelta(minutes=10), "EUR/USD", ["macro"])  # after last bar
    assert align_event(event, series, horizons_minutes=[1]) is None


def test_alignment_respects_max_entry_lag():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    # bars only every hour; event wants a price but nearest is >60m away
    series = PriceSeries(
        pair="EUR/USD",
        times=[start, start + timedelta(hours=3)],
        prices=[1.0, 1.02],
    )
    event = _event(start + timedelta(minutes=1), "EUR/USD", ["macro"])
    assert align_event(event, series, horizons_minutes=[60], max_entry_lag_minutes=60) is None


# ---------------------------------------------------------------- atlas

def _shock_dataset():
    """Two categories: 'shock' reliably +50bp at 1m; 'calm' symmetric noise ~0."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    series_by_pair = {}
    events = []
    # shock events: price jumps +0.5% right after entry
    for i in range(8):
        pair = f"P{i}"
        s = start + timedelta(days=i)
        series_by_pair[pair] = _minute_series(pair, s, [1.0, 1.005, 1.005])
        events.append(_event(s, pair, ["shock"], event_id=f"shock-{i}"))
    # calm events: alternating tiny +/- moves, mean ~0
    for i in range(8):
        pair = f"Q{i}"
        s = start + timedelta(days=100 + i)
        move = 1.0001 if i % 2 == 0 else 0.9999
        series_by_pair[pair] = _minute_series(pair, s, [1.0, move, move])
        events.append(_event(s, pair, ["calm"], event_id=f"calm-{i}"))
    return events, series_by_pair


def test_atlas_flags_significant_category():
    events, series_by_pair = _shock_dataset()
    obs = align_events(events, series_by_pair, horizons_minutes=[1])
    # mean_adjust off so we test raw category signal against zero directly.
    atlas = build_atlas(obs, horizons_minutes=[1], mean_adjust=False)

    shock = atlas["shock"].horizons[1]
    calm = atlas["calm"].horizons[1]
    assert shock.n == 8
    assert shock.mean_return_bps > 40         # ~+50 bps
    assert shock.p_value < 0.01               # significant
    assert shock.hit_rate == 1.0
    assert calm.p_value > 0.05                # not significant


def test_atlas_multilabel_event_contributes_to_all_categories():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    series = _minute_series("EUR/USD", start, [1.0, 1.002, 1.002])
    events = [_event(start, "EUR/USD", ["central_bank", "rate_decision"])]
    obs = align_events(events, {"EUR/USD": series}, horizons_minutes=[1])
    atlas = build_atlas(obs, horizons_minutes=[1], mean_adjust=False)
    assert "central_bank" in atlas
    assert "rate_decision" in atlas
    assert atlas["central_bank"].horizons[1].n == 1

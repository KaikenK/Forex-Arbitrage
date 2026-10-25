"""Tests for intraday loaders + reaction/volatility/persistence features. No network."""
from __future__ import annotations

import lzma
import struct
from datetime import UTC, datetime, timedelta

from research.data.prices_intraday import load_histdata_m1_csv, parse_bi5
from research.eventstudy import Event, PriceSeries, compute_reaction_features


# ---------------------------------------------------------------- HistData loader

def test_load_histdata_m1_csv_to_utc(tmp_path):
    path = tmp_path / "EURUSD.csv"
    # EST (UTC-5): 12:00:00 EST -> 17:00:00 UTC
    path.write_text(
        "20240102 120000;1.10000;1.10010;1.09990;1.10005;0\n"
        "20240102 120100;1.10005;1.10020;1.10000;1.10018;0\n",
        encoding="utf-8",
    )
    series = load_histdata_m1_csv(path, "EUR/USD")
    assert len(series.times) == 2
    assert series.times[0] == datetime(2024, 1, 2, 17, 0, tzinfo=UTC)
    assert series.prices[0] == 1.10005


# ---------------------------------------------------------------- Dukascopy bi5

def test_parse_bi5_decodes_ticks():
    hour_start = datetime(2024, 1, 2, 13, 0, tzinfo=UTC)
    record = struct.Struct(">IIIff")
    raw = (
        record.pack(0, 110010, 109990, 1.0, 1.0)       # t+0ms, mid=1.10000
        + record.pack(60000, 110030, 110010, 2.0, 2.0)  # t+60s, mid=1.10020
    )
    ticks = parse_bi5(lzma.compress(raw), hour_start, point=1e-5)
    assert len(ticks) == 2
    assert ticks[0][0] == hour_start
    assert abs(ticks[0][1] - 1.10000) < 1e-9
    assert ticks[1][0] == hour_start + timedelta(seconds=60)


# ---------------------------------------------------------------- reaction features

def _minute_series(pair, start, prices):
    times = [start + timedelta(minutes=i) for i in range(len(prices))]
    return PriceSeries(pair=pair, times=times, prices=prices)


def test_reaction_features_volatility_and_persistence():
    start = datetime(2024, 1, 2, 12, 0, tzinfo=UTC)
    # 30 calm pre bars (flat 1.0), then a volatile upward drift post-event.
    pre = [1.0] * 31
    post = [1.001, 1.0005, 1.002, 1.0015, 1.003, 1.004, 1.0035, 1.005]  # noisy up
    post += [1.006 + 0.0001 * i for i in range(60)]                      # continue up to +60m
    series = _minute_series("EUR/USD", start, pre + post)
    event = Event(event_id="e1", timestamp=start + timedelta(minutes=30), pair="EUR/USD",
                  categories=("macro",))

    feats = compute_reaction_features(event, series, horizons_minutes=[1, 5, 60], pre_window_minutes=20)
    assert feats is not None
    assert feats.returns[5] > 0                     # upward reaction
    assert feats.returns[60] > 0
    assert feats.realized_vol_pre == 0.0            # flat pre-window
    assert feats.vol_ratio == {}                    # pre-vol 0 -> ratio undefined (guarded)
    assert feats.persistence_ratio is not None
    assert feats.persistence_ratio > 0              # 60m same sign as 5m -> persists
    assert feats.reversal is False


def test_reaction_features_detects_reversal():
    start = datetime(2024, 1, 2, 12, 0, tzinfo=UTC)
    pre = [1.0 + 0.0001 * (i % 2) for i in range(21)]   # tiny noise -> nonzero pre-vol
    # up then fully reverse below entry
    post = [1.002, 1.003, 1.0025, 1.001, 0.999, 0.998, 0.997]
    series = _minute_series("EUR/USD", start, pre + post)
    event = Event(event_id="e2", timestamp=start + timedelta(minutes=20), pair="EUR/USD",
                  categories=("macro",))

    feats = compute_reaction_features(event, series, horizons_minutes=[1, 6], pre_window_minutes=15,
                                      persistence_short=1, persistence_long=6)
    assert feats is not None
    assert feats.returns[1] > 0 and feats.returns[6] < 0
    assert feats.reversal is True
    assert feats.vol_ratio[6] > 0                    # pre-vol nonzero -> ratio defined

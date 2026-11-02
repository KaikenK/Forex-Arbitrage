"""Window-30 (C35): 30-minute news window vs the next 30-60 minutes. Synthetic data, no network."""
from __future__ import annotations

from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
import pytest

from research import run_window30 as w

T0 = datetime(2024, 3, 4, 12, 0, tzinfo=UTC).timestamp()          # a Monday


def _events(rows):
    return pd.DataFrame([{"timestamp": pd.Timestamp(T0 + dt * 60, unit="s", tz="UTC"), "currencies": c,
                          "polarity": p, "confidence": conf} for dt, c, p, conf in rows])


def test_orientation_names_exactly_one_side():
    assert w.orientation("EUR/USD", ["EUR"]) == 1 and w.orientation("EUR/USD", ["USD"]) == -1
    assert w.orientation("EUR/USD", ["EUR", "USD"]) == 0 and w.orientation("USD/JPY", ["GBP"]) == 0


def test_window_bias_lag_recency_and_activity():
    news = w.build_pair_news(_events([
        (-50, "EUR", 0.9, 1.0),        # before the window (t-45, t-15]: ignored
        (-40, "USD", 0.5, 1.0),        # old in the window: recency max(0.1, 1 - 25/30); USD = quote -> -0.5
        (-16, "EUR", 0.8, 1.0),        # newest in the window: recency ~0.97
        (-20, "EUR;USD", 0.7, 1.0),    # both currencies: activity only, no direction
        (-10, "EUR", -1.0, 1.0),       # inside the 15 min lag: ignored
    ]), pairs=["EUR/USD", "USD/JPY"])
    bias, activity = w.window_signal(news["EUR/USD"], np.array([T0]))
    r_old, r_new = 1 - 25 / 30, 1 - 1 / 30
    assert activity[0] == 3
    assert bias[0] == pytest.approx((r_old * -0.5 + r_new * 0.8) / (r_old + r_new))
    jpy_bias, jpy_activity = w.window_signal(news["USD/JPY"], np.array([T0]))
    r_mid = 1 - 5 / 30                     # "EUR;USD" names only USD of this pair: oriented, USD = base
    assert jpy_activity[0] == 2 and jpy_bias[0] == pytest.approx((r_old * 0.5 + r_mid * 0.7) / (r_old + r_mid))
    empty_bias, empty_activity = w.window_signal(news["EUR/USD"], np.array([T0 + 6 * 3600]))
    assert np.isnan(empty_bias[0]) and empty_activity[0] == 0
    assert w.caution(np.array([0.5, np.nan]), np.array([10, 0])) == pytest.approx([0.5, 0.0])


def test_signal_times_and_missing_days():
    times = w.signal_times_30(date(2024, 3, 1), date(2024, 3, 4))      # Fri, Sat, Sun, Mon
    assert len(times) == 2 * 48
    kept = w.drop_missing(times, ["2024-03-01"])
    friday = datetime(2024, 3, 1, tzinfo=UTC).timestamp()
    # Friday 00:00's window (Thu 23:15, Thu 23:45] does not touch Friday; 00:30's does.
    assert len(kept) == 49 and kept[0] == friday and kept[1] == datetime(2024, 3, 4, tzinfo=UTC).timestamp()


def test_abnormal_move_uses_the_same_clock_on_earlier_days():
    days = 30
    times = np.array([T0 + d * 86400 for d in range(days)] + [T0 + 1800 + d * 86400 for d in range(days)])
    returns = np.full(len(times), 0.001)
    returns[days - 1] = 0.004                                         # a spike on the last day at 12:00
    ratio = w.abnormal_move(times, returns)
    assert np.isnan(ratio[:10]).all()                                 # fewer than 10 earlier days
    assert ratio[days - 1] == pytest.approx(4.0) and ratio[2 * days - 1] == pytest.approx(1.0)


def test_release_flag_window():
    releases = {"USD": [T0 + 600.0], "JPY": [T0 - 3600.0]}
    flags = w.release_flags(releases, "EUR/USD", np.array([T0, T0 + 3600, T0 - 1200]), 30)
    assert list(flags) == [True, False, True]


def _synthetic_panel(seed: int = 7, *, planted: bool = True) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    times = w.signal_times_30(date(2024, 1, 1), date(2024, 4, 30))
    n = len(times)
    news, returns = {}, {h: {} for h in w.HORIZONS_MIN}
    for pair in ("EUR/USD", "USD/JPY"):
        stamps = np.sort(rng.choice(times - 25 * 60, size=n // 3, replace=False))
        directions = rng.uniform(-1, 1, size=len(stamps))
        news[pair] = w.PairNews(times=list(stamps), directions=list(directions),
                                confidences=list(np.ones(len(stamps))))
        bias, activity = w.window_signal(news[pair], times)
        for h in w.HORIZONS_MIN:
            if planted:
                vol = 0.0005 * (1 + activity)                         # news activity raises volatility
                returns[h][pair] = 0.0004 * np.nan_to_num(bias) + rng.normal(0, 1, n) * vol
            else:
                returns[h][pair] = rng.normal(0, 0.0005, n)
    return w.build_panel(times, news, returns, {"USD": [float(times[100])]})


def test_planted_signal_is_found_and_bh_applied():
    out = w.run_tests(_synthetic_panel())
    tests = out["tests"]
    assert set(tests) == {f"T{i}_{name}_{h}" for i, name in ((1, "direction"), (2, "activity"), (3, "caution"))
                          for h in w.HORIZONS_MIN}
    assert tests["T1_direction_30"]["ic"] > 0 and tests["T1_direction_30"]["p"] < 0.01
    assert tests["T2_activity_30"]["beta"] > 0 and tests["T2_activity_30"]["p"] < 0.01
    assert all("q_bh" in r for r in tests.values() if r.get("p") is not None)
    assert out["coverage"]["rows_with_bias"] <= out["coverage"]["rows_with_activity"] <= out["coverage"]["signal_rows"]


def test_no_signal_is_not_found():
    tests = w.run_tests(_synthetic_panel(planted=False))["tests"]
    assert tests["T1_direction_30"]["p"] > 0.05 and tests["T2_activity_30"]["p"] > 0.05

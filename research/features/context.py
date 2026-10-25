"""Leak-safe market-context features known at the event timestamp.

Every value uses `price_as_of(ts)` (last bar <= ts) so nothing from after the event leaks in.
Covariate feeds (DXY, VIX, oil, US10Y, ...) are passed as daily PriceSeries; the builder emits
level + 1d/5d return + 20d realized vol for each, plus the pair's own realized vol and calendar
session/time features.
"""
from __future__ import annotations

import bisect
from datetime import datetime, timedelta
from typing import Callable

import numpy as np

from research.eventstudy.schema import PriceSeries

MarketContextFn = Callable[[str, datetime], dict]


def _asof_prices(series: PriceSeries, ts: datetime, n: int) -> list[float]:
    """Last n prices with time <= ts (most recent last)."""
    right = bisect.bisect_right(series.times, ts)
    left = max(0, right - n)
    return series.prices[left:right]


def _ret(prices: list[float], lookback: int) -> float | None:
    if len(prices) <= lookback or prices[-1 - lookback] == 0:
        return None
    return prices[-1] / prices[-1 - lookback] - 1.0


def _realized_vol(prices: list[float]) -> float | None:
    if len(prices) < 3:
        return None
    rets = [prices[i] / prices[i - 1] - 1.0 for i in range(1, len(prices)) if prices[i - 1]]
    return float(np.std(rets, ddof=1)) if len(rets) >= 2 else None


def session_features(ts: datetime) -> dict:
    """Trading session, overlap, and calendar-time features from a UTC timestamp."""
    hour = ts.hour
    tokyo, london, new_york = (0 <= hour < 9), (7 <= hour < 16), (13 <= hour < 22)
    if london and new_york:
        session = "London/NY"
    elif tokyo and london:
        session = "Tokyo/London"
    elif new_york:
        session = "NewYork"
    elif london:
        session = "London"
    elif tokyo:
        session = "Tokyo"
    else:
        session = "Off"
    return {
        "hour_utc": hour,
        "day_of_week": ts.weekday(),
        "is_weekend": ts.weekday() >= 5,
        "session": session,
        "overlap_flag": session in {"London/NY", "Tokyo/London"},
    }


def build_market_context_fn(
    covariates: dict[str, PriceSeries],
    series_by_pair: dict[str, PriceSeries] | None = None,
    *,
    rvol_window: int = 20,
) -> MarketContextFn:
    series_by_pair = series_by_pair or {}

    def context(pair: str, ts: datetime) -> dict:
        row: dict = dict(session_features(ts))
        for name, series in covariates.items():
            prices = _asof_prices(series, ts, rvol_window + 1)
            key = name.lower()
            row[f"{key}_level"] = prices[-1] if prices else None
            row[f"{key}_ret_1d"] = _ret(prices, 1)
            row[f"{key}_ret_5d"] = _ret(prices, 5)
            row[f"{key}_rvol_20d"] = _realized_vol(prices)
        pair_series = series_by_pair.get(pair)
        if pair_series is not None:
            row["pair_rvol_20d"] = _realized_vol(_asof_prices(pair_series, ts, rvol_window + 1))
        else:
            row["pair_rvol_20d"] = None
        return row

    return context

"""Multi-horizon reaction, volatility, and persistence features from intraday prices.

For each event this computes, leak-safe (entry = first bar at/after publication):
  - forward returns over {1m, 5m, 15m, 30m, 1h, 4h, 1d} (or supplied horizons);
  - a volatility response: realized vol of post-event minute returns vs a pre-event baseline
    (vol_ratio > 1 means the event raised volatility);
  - persistence: whether the short-horizon move continues or reverses at a longer horizon.

These become prediction targets + features in the ML stage. Abnormal-return adjustment
(market model with DXY/oil/yields) composes on top in a later milestone.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import timedelta

import numpy as np

from .schema import Event, PriceSeries

DEFAULT_HORIZONS = [1, 5, 15, 30, 60, 240, 1440]


@dataclass(frozen=True)
class ReactionFeatures:
    event_id: str
    pair: str
    entry_time: object
    returns: dict[int, float] = field(default_factory=dict)
    realized_vol_pre: float = 0.0
    realized_vol_post: dict[int, float] = field(default_factory=dict)
    vol_ratio: dict[int, float] = field(default_factory=dict)
    persistence_ratio: float | None = None   # ret_long / ret_short (>0 continues, <0 reverses)
    reversal: bool | None = None

    def to_row(self) -> dict:
        row = {
            "event_id": self.event_id,
            "pair": self.pair,
            "realized_vol_pre": self.realized_vol_pre,
            "persistence_ratio": self.persistence_ratio,
            "reversal": self.reversal,
        }
        for horizon, value in self.returns.items():
            row[f"ret_{horizon}m"] = value
        for horizon, value in self.vol_ratio.items():
            row[f"volratio_{horizon}m"] = value
        return row


def _bar_returns(series: PriceSeries, t0, t1) -> list[float]:
    """Consecutive simple returns of bars with t0 <= time <= t1."""
    left = bisect.bisect_left(series.times, t0)
    right = bisect.bisect_right(series.times, t1)
    prices = series.prices[left:right]
    return [prices[i] / prices[i - 1] - 1.0 for i in range(1, len(prices)) if prices[i - 1]]


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    return float(np.std(values, ddof=1))


def compute_reaction_features(
    event: Event,
    series: PriceSeries,
    *,
    horizons_minutes: list[int] = DEFAULT_HORIZONS,
    pre_window_minutes: int = 30,
    max_entry_lag_minutes: int = 10,
    persistence_short: int = 5,
    persistence_long: int = 60,
) -> ReactionFeatures | None:
    entry = series.price_at_or_after(event.timestamp)
    if entry is None:
        return None
    entry_time, entry_price = entry
    if entry_time - event.timestamp > timedelta(minutes=max_entry_lag_minutes) or entry_price == 0:
        return None

    vol_pre = _std(_bar_returns(series, entry_time - timedelta(minutes=pre_window_minutes), entry_time))

    returns: dict[int, float] = {}
    realized_vol_post: dict[int, float] = {}
    vol_ratio: dict[int, float] = {}
    for horizon in horizons_minutes:
        exit_bar = series.price_at_or_after(entry_time + timedelta(minutes=horizon))
        if exit_bar is None:
            continue
        returns[horizon] = exit_bar[1] / entry_price - 1.0
        post_vol = _std(_bar_returns(series, entry_time, entry_time + timedelta(minutes=horizon)))
        realized_vol_post[horizon] = post_vol
        if vol_pre > 0:
            vol_ratio[horizon] = post_vol / vol_pre

    persistence_ratio: float | None = None
    reversal: bool | None = None
    if persistence_short in returns and persistence_long in returns and returns[persistence_short] != 0:
        persistence_ratio = returns[persistence_long] / returns[persistence_short]
        reversal = (returns[persistence_long] * returns[persistence_short]) < 0

    return ReactionFeatures(
        event_id=event.event_id,
        pair=event.pair,
        entry_time=entry_time,
        returns=returns,
        realized_vol_pre=vol_pre,
        realized_vol_post=realized_vol_post,
        vol_ratio=vol_ratio,
        persistence_ratio=persistence_ratio,
        reversal=reversal,
    )

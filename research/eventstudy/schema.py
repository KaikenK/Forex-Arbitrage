"""Typed primitives for the event-study pipeline.

Timestamps are timezone-aware UTC. `Event.timestamp` is the *publication* time — the
first moment the information was actionable — which is what leak-safe alignment keys on.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Event:
    event_id: str
    timestamp: datetime          # UTC publication time
    pair: str
    categories: tuple[str, ...] = ()   # multi-label (an event may span categories)
    surprise: float | None = None      # standardized (actual - consensus) / std, if scheduled
    source: str = ""
    headline: str = ""
    # How precisely `timestamp` is known, in minutes. 0 = exact (scheduled calendar releases
    # such as NFP at 13:30:00). GDELT reports crawl time quantized to 15-minute buckets, so its
    # events carry 15 and cannot support sub-15-minute reaction horizons.
    timestamp_precision_minutes: float = 0.0

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("Event.timestamp must be timezone-aware (UTC)")


@dataclass
class PriceSeries:
    """Ascending-by-time price series for one pair. `times` must be sorted."""

    pair: str
    times: list[datetime]
    prices: list[float]

    def __post_init__(self) -> None:
        if len(self.times) != len(self.prices):
            raise ValueError("times and prices must be the same length")
        if any(b < a for a, b in zip(self.times, self.times[1:])):
            raise ValueError("PriceSeries.times must be sorted ascending")

    def price_at_or_after(self, ts: datetime) -> tuple[datetime, float] | None:
        """First bar with time >= ts (the earliest tradable price once info is public)."""
        index = bisect.bisect_left(self.times, ts)
        if index >= len(self.times):
            return None
        return self.times[index], self.prices[index]

    def price_as_of(self, ts: datetime) -> tuple[datetime, float] | None:
        """Last bar with time <= ts (state of the market just before/at a moment)."""
        index = bisect.bisect_right(self.times, ts) - 1
        if index < 0:
            return None
        return self.times[index], self.prices[index]


@dataclass(frozen=True)
class AlignedObservation:
    """One event aligned to its leak-safe forward returns (per horizon, in minutes)."""

    event: Event
    entry_time: datetime
    entry_price: float
    horizon_returns: dict[int, float] = field(default_factory=dict)
    abnormal_returns: dict[int, float] = field(default_factory=dict)

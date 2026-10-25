"""Event-windowed intraday fetching: pull only the hours the event study actually needs.

Fetching every hour of tick data for 6 pairs x 8 years is ~420k requests. The event study only
ever reads a short window around each event, so this plans the minimal set of (pair, hour) pairs
covering all event windows, deduplicated across overlapping events. Clustered events (macro
releases fire together) collapse into shared hours, which is where most of the saving comes from.

Timestamp precision drives which horizons are defensible:
  - Scheduled calendar releases are exact (precision 0) -> 1m/5m horizons are meaningful.
  - GDELT reports crawl time quantized to 15-minute buckets (precision 15) -> the true
    publication time is unknown within that bucket, so sub-bucket horizons measure drift rather
    than reaction. `min_valid_horizon` enforces this instead of silently reporting noise.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from research.eventstudy.schema import Event, PriceSeries

from .intraday_pipeline import build_intraday_series, fetch_hour_cached, resample_to_bars

# A horizon is only reported if it is at least this multiple of the timestamp uncertainty.
PRECISION_SAFETY_FACTOR = 2.0


def min_valid_horizon(precision_minutes: float) -> float:
    """Shortest reaction horizon that is defensible given timestamp uncertainty."""
    if precision_minutes <= 0:
        return 0.0
    return precision_minutes * PRECISION_SAFETY_FACTOR


def valid_horizons(horizons_minutes: list[int], precision_minutes: float) -> list[int]:
    floor = min_valid_horizon(precision_minutes)
    return [h for h in horizons_minutes if h >= floor]


@dataclass(frozen=True)
class FetchPlan:
    hours_by_pair: dict[str, list[datetime]]

    @property
    def total_hours(self) -> int:
        return sum(len(hours) for hours in self.hours_by_pair.values())

    def summary(self) -> str:
        parts = [f"{pair}:{len(hours)}" for pair, hours in sorted(self.hours_by_pair.items())]
        return f"{self.total_hours} hours ({', '.join(parts)})"


def plan_fetch_hours(
    events: list[Event],
    *,
    pre_hours: int = 2,
    post_hours: int = 6,
    skip_saturday: bool = True,
) -> FetchPlan:
    """Minimal deduplicated set of (pair, hour) needed to cover every event window."""
    hours_by_pair: dict[str, set[datetime]] = {}
    for event in events:
        anchor = event.timestamp.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        bucket = hours_by_pair.setdefault(event.pair, set())
        for offset in range(-pre_hours, post_hours + 1):
            hour = anchor + timedelta(hours=offset)
            if skip_saturday and hour.weekday() == 5:
                continue
            bucket.add(hour)
    return FetchPlan({pair: sorted(hours) for pair, hours in hours_by_pair.items()})


def fetch_plan(
    plan: FetchPlan,
    cache_dir: Path,
    *,
    interval_minutes: int = 1,
    fetch_raw_fn=None,
    progress=None,
) -> dict[str, PriceSeries]:
    """Execute a fetch plan, returning one minute-bar PriceSeries per pair.

    Every hour is cached on disk, so re-running is offline and incremental: a partially
    completed plan resumes without re-downloading what it already has.
    """
    cache_dir = Path(cache_dir)
    series_by_pair: dict[str, PriceSeries] = {}
    for pair, hours in plan.hours_by_pair.items():
        ticks: list[tuple[datetime, float]] = []
        failures = 0
        for index, hour in enumerate(hours):
            try:
                ticks.extend(fetch_hour_cached(pair, hour, cache_dir, fetch_raw_fn=fetch_raw_fn))
            except Exception:
                failures += 1
            if progress is not None and (index + 1) % 25 == 0:
                progress(pair, index + 1, len(hours), failures)
        bars = resample_to_bars(ticks, interval_minutes)
        series_by_pair[pair] = PriceSeries(
            pair=pair, times=[t for t, _ in bars], prices=[p for _, p in bars]
        )
        if progress is not None:
            progress(pair, len(hours), len(hours), failures)
    return series_by_pair


def estimate_savings(plan: FetchPlan, events: list[Event], *, pre_hours: int, post_hours: int) -> dict:
    """Compare planned hours against naive per-event fetching (shows dedup benefit)."""
    naive = len(events) * (pre_hours + post_hours + 1)
    planned = plan.total_hours
    return {
        "events": len(events),
        "naive_hours": naive,
        "planned_hours": planned,
        "reduction": 1.0 - (planned / naive) if naive else 0.0,
    }

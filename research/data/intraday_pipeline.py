"""Intraday price pipeline: fetch -> cache -> resample to bars -> PriceSeries.

Dukascopy serves one LZMA-compressed tick file per (pair, hour) with no key. Downloading
years of ticks is a bulk job, so every hour is cached to disk on first fetch and re-read
afterward, making repeat pipeline runs offline and reproducible.

Ticks are resampled to fixed-interval bars (default 1 minute) because the event study needs a
regular grid: `PriceSeries.price_at_or_after` assumes bars, and minute bars match the horizons
(1m/5m/15m/30m/1h/4h) without carrying tick-level microstructure noise into every return.
"""
from __future__ import annotations

import os
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from research.eventstudy.schema import PriceSeries

from .prices_intraday import fetch_dukascopy_hour, parse_bi5, ticks_to_series

# Dukascopy quotes JPY pairs with 3 decimals, most others with 5.
POINT_BY_PAIR = {
    "USD/JPY": 1e-3, "EUR/JPY": 1e-3, "GBP/JPY": 1e-3, "AUD/JPY": 1e-3, "CHF/JPY": 1e-3,
}
DEFAULT_POINT = 1e-5


def point_for(pair: str) -> float:
    return POINT_BY_PAIR.get(pair.upper(), DEFAULT_POINT)


def _cache_path(cache_dir: Path, pair: str, hour: datetime) -> Path:
    slug = pair.replace("/", "").upper()
    return cache_dir / slug / f"{hour:%Y%m%d_%H}.bi5"


def fetch_raw_hour(pair: str, hour: datetime) -> bytes:
    """Download one hour of raw .bi5 bytes. Empty bytes means the hour has no data."""
    import urllib.request

    url = (
        f"https://datafeed.dukascopy.com/datafeed/{pair.replace('/', '').upper()}/"
        f"{hour.year:04d}/{hour.month - 1:02d}/{hour.day:02d}/{hour.hour:02d}h_ticks.bi5"
    )
    # Dukascopy refuses unknown user agents, so present a browser-style one.
    request = urllib.request.Request(url, headers={"user-agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def _normalize_hour(hour: datetime) -> datetime:
    return hour.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def is_hour_cached(pair: str, hour: datetime, cache_dir: Path) -> bool:
    return _cache_path(Path(cache_dir), pair, _normalize_hour(hour)).exists()


def ensure_hour_cached(
    pair: str,
    hour: datetime,
    cache_dir: Path,
    *,
    fetch_raw_fn=None,
) -> bool:
    """Download one hour into the cache if absent. Returns True if it was downloaded now.

    Does not decode: a bulk prefetch only needs the bytes on disk, and LZMA-decoding
    thousands of hours it will not read yet would be wasted CPU.

    The write is atomic (temp file, then rename). A run killed mid-write must never leave a
    truncated .bi5, because a later run would trust it as a complete hour of ticks.
    """
    hour = _normalize_hour(hour)
    path = _cache_path(Path(cache_dir), pair, hour)
    if path.exists():
        return False

    fetch = fetch_raw_fn or fetch_raw_hour
    raw = fetch(pair, hour)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        temp_path.write_bytes(raw or b"")
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return True


def fetch_hour_cached(
    pair: str,
    hour: datetime,
    cache_dir: Path,
    *,
    fetch_raw_fn=None,
) -> list[tuple[datetime, float]]:
    """Fetch one hour of ticks, using the on-disk cache when present.

    The cache stores the raw .bi5 payload so a cached hour decodes to the same ticks as a
    fresh download. An empty cached file means the hour genuinely had no ticks (weekend or
    market closure) and is not re-requested.
    """
    hour = _normalize_hour(hour)
    ensure_hour_cached(pair, hour, cache_dir, fetch_raw_fn=fetch_raw_fn)
    raw = _cache_path(Path(cache_dir), pair, hour).read_bytes()
    return parse_bi5(raw, hour, point=point_for(pair)) if raw else []


def resample_to_bars(
    ticks: list[tuple[datetime, float]],
    interval_minutes: int = 1,
) -> list[tuple[datetime, float]]:
    """Last tick price within each interval bucket -> (bar_close_time, close_price)."""
    if not ticks:
        return []
    step = timedelta(minutes=interval_minutes)
    buckets: dict[datetime, tuple[datetime, float]] = {}
    for stamp, price in ticks:
        anchor = stamp.replace(second=0, microsecond=0)
        anchor -= timedelta(minutes=anchor.minute % interval_minutes)
        current = buckets.get(anchor)
        if current is None or stamp >= current[0]:
            buckets[anchor] = (stamp, price)
    return [(anchor, buckets[anchor][1]) for anchor in sorted(buckets)]


def build_intraday_series(
    pair: str,
    start: datetime,
    end: datetime,
    cache_dir: Path,
    *,
    interval_minutes: int = 1,
    fetch_raw_fn=None,
    on_error=None,
) -> PriceSeries:
    """Assemble a minute-bar PriceSeries for [start, end) from cached/fetched Dukascopy ticks."""
    cache_dir = Path(cache_dir)
    ticks: list[tuple[datetime, float]] = []
    cursor = start.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    end = end.astimezone(UTC)
    while cursor < end:
        if cursor.weekday() != 5:  # skip Saturday (market closed)
            try:
                ticks.extend(fetch_hour_cached(pair, cursor, cache_dir, fetch_raw_fn=fetch_raw_fn))
            except Exception as exc:
                # A missing hour must not abort a multi-day build, but surface it if asked.
                if on_error is not None:
                    on_error(cursor, exc)
        cursor += timedelta(hours=1)
    bars = resample_to_bars(ticks, interval_minutes)
    return PriceSeries(pair=pair, times=[t for t, _ in bars], prices=[p for _, p in bars])

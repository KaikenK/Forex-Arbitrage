"""Intraday FX price loaders -> event-study `PriceSeries` (minute/tick resolution, UTC).

Two free sources:
  - HistData M1 ASCII dumps (semicolon CSV, US Eastern wall clock) — reproducible static files.
  - Dukascopy .bi5 tick files (LZMA-compressed, public datafeed, no key) — programmatic.

Both normalize to UTC so events align precisely to market timestamps.
"""
from __future__ import annotations

import lzma
import struct
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from research.eventstudy.schema import PriceSeries

# HistData's files are US Eastern wall-clock time *with* DST, although HistData describes
# them as "EST without DST". Verified 2026-10-24 (RESEARCH_TRACKER §6): parsed with a fixed
# UTC-5, releases in the US DST season showed no reaction at +1 min and the full reaction at
# +61 min (winter releases reacted at +1 min), i.e. summer bars were labelled an hour late.
HISTDATA_SOURCE_ZONE = ZoneInfo("America/New_York")
_UTC_OFFSET_BY_LOCAL_HOUR: dict[tuple[int, int, int, int], timedelta] = {}


def _eastern_utc_offset(year: int, month: int, day: int, hour: int) -> timedelta:
    """UTC offset of a New York local hour, cached: ~70k distinct hours cover the study."""
    key = (year, month, day, hour)
    offset = _UTC_OFFSET_BY_LOCAL_HOUR.get(key)
    if offset is None:
        offset = datetime(year, month, day, hour, tzinfo=HISTDATA_SOURCE_ZONE).utcoffset()
        _UTC_OFFSET_BY_LOCAL_HOUR[key] = offset
    return offset


def parse_histdata_m1_line(
    line: str,
    *,
    tz_offset_hours: float | None = None,
) -> tuple[datetime, float] | None:
    """One 'YYYYMMDD HHMMSS;O;H;L;C;V' line -> (bar time in UTC, close), or None if malformed.

    The stamp is read as New York wall-clock time (DST-aware) unless `tz_offset_hours`
    forces a fixed offset. The DST switch happens on Sunday 02:00 local, when FX is closed,
    so the repeated/skipped local hour never contains bars.
    """
    stripped = line.strip()
    if not stripped:
        return None
    fields = stripped.split(";")
    if len(fields) < 5:
        return None
    stamp = fields[0].strip()
    try:
        close = float(fields[4])
        # Fast path: fixed-width 'YYYYMMDD HHMMSS' sliced directly. strptime is ~5x slower,
        # which matters when streaming ~12M bars for the full study.
        if len(stamp) == 15 and stamp[8] == " ":
            naive = datetime(
                int(stamp[0:4]), int(stamp[4:6]), int(stamp[6:8]),
                int(stamp[9:11]), int(stamp[11:13]), int(stamp[13:15]),
            )
        else:
            naive = datetime.strptime(stamp, "%Y%m%d %H%M%S")
    except ValueError:
        return None
    if tz_offset_hours is not None:
        offset = timedelta(hours=tz_offset_hours)
    else:
        offset = _eastern_utc_offset(naive.year, naive.month, naive.day, naive.hour)
    return (naive - offset).replace(tzinfo=UTC), close


def load_histdata_m1_csv(
    path: str | Path,
    pair: str,
    *,
    tz_offset_hours: float | None = None,
) -> PriceSeries:
    """Parse a HistData Generic-ASCII M1 file: 'YYYYMMDD HHMMSS;O;H;L;C;V' -> close series (UTC)."""
    times: list[datetime] = []
    prices: list[float] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        parsed = parse_histdata_m1_line(line, tz_offset_hours=tz_offset_hours)
        if parsed is None:
            continue
        times.append(parsed[0])
        prices.append(parsed[1])
    order = sorted(range(len(times)), key=times.__getitem__)
    return PriceSeries(pair=pair, times=[times[i] for i in order], prices=[prices[i] for i in order])


_BI5_RECORD = struct.Struct(">IIIff")  # ms_offset, ask, bid, ask_vol, bid_vol


def parse_bi5(raw: bytes, hour_start: datetime, *, point: float = 1e-5) -> list[tuple[datetime, float]]:
    """Decode a Dukascopy .bi5 hour file into (utc_time, mid_price) ticks."""
    payload = lzma.decompress(raw)
    ticks: list[tuple[datetime, float]] = []
    for offset in range(0, len(payload) - _BI5_RECORD.size + 1, _BI5_RECORD.size):
        ms, ask, bid, _ask_vol, _bid_vol = _BI5_RECORD.unpack_from(payload, offset)
        mid = (ask + bid) / 2.0 * point
        ticks.append((hour_start + timedelta(milliseconds=ms), mid))
    return ticks


def ticks_to_series(ticks: list[tuple[datetime, float]], pair: str) -> PriceSeries:
    ordered = sorted(ticks, key=lambda item: item[0])
    return PriceSeries(pair=pair, times=[t for t, _ in ordered], prices=[p for _, p in ordered])


def fetch_dukascopy_hour(
    pair: str,
    when: datetime,
    *,
    point: float = 1e-5,
) -> list[tuple[datetime, float]]:
    """Fetch one hour of Dukascopy ticks (network). Month is 0-indexed in their path scheme."""
    import urllib.request

    hour_start = when.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    url = (
        f"https://datafeed.dukascopy.com/datafeed/{pair.replace('/', '').upper()}/"
        f"{hour_start.year:04d}/{hour_start.month - 1:02d}/{hour_start.day:02d}/"
        f"{hour_start.hour:02d}h_ticks.bi5"
    )
    request = urllib.request.Request(url, headers={"user-agent": "fx-research/0.1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read()
    if not raw:
        return []
    return parse_bi5(raw, hour_start, point=point)

"""Economic-calendar / surprise connector (scheduled events).

Unified schema: timestamp, country, currency, event_name, category, importance, previous,
forecast, actual, surprise (actual - forecast), standardized_surprise.

Primary provider is a static calendar CSV dump (e.g. a ForexFactory / Investing / Kaggle
historical export) because static dumps are *reproducible* — no scrape fragility or ToS risk,
and the exact snapshot can be versioned for the paper. Columns are mapped flexibly.

Standardized surprise follows Andersen-Bollerslev-Diebold-Vega (2003): divide each event's
(actual - forecast) by the standard deviation of that event's historical surprises, making
surprises comparable across indicators with different units/scales.

Design note (graduate-grade): forecast must be the *pre-release consensus*, not a later
revision, or the surprise leaks. Static dumps captured at release time satisfy this; FRED/ALFRED
gives leak-safe first-release *actuals* but not survey consensus, so it validates actuals rather
than sourcing surprise.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .base import RawEvent
from .release_schedule import impute_release_times
from .tagging import tag_categories

_CURRENCY_COUNTRY = {
    "USD": "United States", "EUR": "Euro Area", "JPY": "Japan", "GBP": "United Kingdom",
    "CHF": "Switzerland", "AUD": "Australia", "NZD": "New Zealand", "CAD": "Canada",
    "INR": "India", "CNY": "China",
}
_SCALE = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}
_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


@dataclass(frozen=True)
class CalendarEvent:
    timestamp: datetime
    currency: str
    event_name: str
    country: str = ""
    importance: str = ""
    categories: tuple[str, ...] = ()
    previous: float | None = None
    forecast: float | None = None
    actual: float | None = None
    surprise: float | None = None
    standardized_surprise: float | None = None
    # 0 = exact release time (usable for 1m/5m horizons). 1440 = the source only gave a
    # date (stamped 23:59:59 or 00:00:00); such rows must not drive intraday analysis.
    timestamp_precision_minutes: float = 0.0
    # "source" = time as observed in the dump; "schedule" = a missing time filled from the
    # agency's documented release clock (`release_schedule.py`).
    timestamp_source: str = "source"
    # The row's date in the source's own clock, kept so a missing time can be filled on the
    # right day (the UTC timestamp of a 00:00 placeholder falls on the previous date).
    source_local_date: date | None = None
    # Release falls inside the pre-registered pandemic window (see PANDEMIC_WINDOW_START).
    in_pandemic_window: bool = False

    @property
    def has_exact_time(self) -> bool:
        return self.timestamp_precision_minutes == 0.0

    @property
    def event_id(self) -> str:
        key = f"{self.currency}|{self.event_name}|{self.timestamp.isoformat()}"
        return "cal-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:14]

    def to_raw_event(self) -> RawEvent:
        return RawEvent(
            event_id=self.event_id,
            timestamp=self.timestamp,
            headline=f"{self.country} {self.event_name}".strip(),
            source="calendar",
            event_type="scheduled",
            currencies=(self.currency,),
            categories=self.categories,
            country=self.country,
            event_name=self.event_name,
            importance=self.importance,
            previous=self.previous,
            forecast=self.forecast,
            actual=self.actual,
            surprise=self.surprise,
            standardized_surprise=self.standardized_surprise,
            scheduled=True,
            timestamp_precision_minutes=self.timestamp_precision_minutes,
            timestamp_source=self.timestamp_source,
            in_pandemic_window=self.in_pandemic_window,
        )


def parse_number(value: object) -> float | None:
    """Parse calendar numerics: '256K', '3.5%', '1.2M', '4,500', '-0.3', '' -> float|None."""
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("%", "")
    if not text or text in {"-", "n/a", "na"}:
        return None
    multiplier = 1.0
    if text[-1].upper() in _SCALE:
        multiplier = _SCALE[text[-1].upper()]
        text = text[:-1]
    if not _NUM_RE.match(text):
        return None
    return float(text) * multiplier


# Local clock times that mean "no real release time", not a genuine release at that moment.
# 23:59:59 is how ForexFactory-style dumps stamp date-only rows. 00:00:00 is where the
# acquired dump puts rows whose time was lost (~30% of rows, every year, including every
# 2018-2025 payrolls release), and what a bare date parses to. A real release at local
# midnight is rare enough that failing closed on it costs far less than trusting the marker.
_DATE_ONLY_MARKERS = frozenset({(23, 59, 59), (0, 0, 0)})
_DATE_ONLY_PRECISION_MINUTES = 1440.0

# The acquired ForexFactory dump writes Tehran wall-clock times. Its UTC offsets were
# generated with stale rules that still apply Iranian DST (+04:30) after Iran abolished it
# in 2022, so 2023+ summer rows are an hour early in UTC. Re-localising the wall clock with
# current tzdata fixes this; for earlier years it reproduces the dump's own offsets.
FOREXFACTORY_SOURCE_ZONE = "Asia/Tehran"


def _parse_timestamp(
    row: dict, tz_offset_hours: float, source_zone: ZoneInfo | None = None,
) -> tuple[datetime, float, date] | None:
    """Parse a calendar timestamp to UTC, how precisely the time is known, and the local date.

    Handles both an ISO-8601 `datetime` column (which may carry its own UTC offset, in which
    case `tz_offset_hours` is ignored because the string is already unambiguous) and separate
    naive `date` / `time` columns. With `source_zone`, any embedded offset is discarded and
    the wall clock is interpreted in that IANA zone instead (see FOREXFACTORY_SOURCE_ZONE).
    """
    raw = str(
        row.get("datetime")
        or row.get("date_time")
        or row.get("timestamp")
        or f"{row.get('date', '')} {row.get('time', '')}"
    ).strip()
    if not raw or raw.casefold() in {"nan", "none"}:
        return None

    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %I:%M%p",
                    "%m/%d/%Y %H:%M", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None

    # A date-only row is flagged from its *local* clock time, before any UTC conversion
    # shifts the marker to some other hour.
    precision = (
        _DATE_ONLY_PRECISION_MINUTES
        if (parsed.hour, parsed.minute, parsed.second) in _DATE_ONLY_MARKERS
        else 0.0
    )
    local_date = parsed.date()

    if source_zone is not None:
        return parsed.replace(tzinfo=source_zone).astimezone(UTC), precision, local_date
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC), precision, local_date
    # Naive input: interpret it in the source's local zone, then convert.
    return (parsed - _timedelta_hours(tz_offset_hours)).replace(tzinfo=UTC), precision, local_date


# "High Impact Expected" -> "high"; keeps the vocabulary small and comparable across sources.
_IMPACT_LEVELS = ("high", "medium", "low", "non-economic")


def _normalize_impact(value: object) -> str:
    lowered = str(value or "").strip().casefold()
    for level in _IMPACT_LEVELS:
        if level in lowered:
            return level
    return ""


def _timedelta_hours(hours: float):
    from datetime import timedelta
    return timedelta(hours=hours)


def parse_calendar_records(
    records: list[dict],
    *,
    tz_offset_hours: float = 0.0,
    source_zone: str | None = None,
    impute_schedule: bool = False,
) -> list[CalendarEvent]:
    """Parse raw calendar rows. `impute_schedule` fills missing times of validated US
    release families from their documented clocks (`release_schedule.py`)."""
    zone = ZoneInfo(source_zone) if source_zone else None
    events: list[CalendarEvent] = []
    for row in records:
        lower = {str(k).strip().lower(): v for k, v in row.items()}
        currency = str(lower.get("currency", "")).strip().upper()
        event_name = str(lower.get("event") or lower.get("event_name") or "").strip()
        parsed = _parse_timestamp(lower, tz_offset_hours, zone)
        if not currency or not event_name or parsed is None:
            continue
        timestamp, precision, local_date = parsed
        forecast = parse_number(lower.get("forecast") or lower.get("consensus"))
        actual = parse_number(lower.get("actual"))
        surprise = (actual - forecast) if (actual is not None and forecast is not None) else None
        country = str(lower.get("country") or _CURRENCY_COUNTRY.get(currency, "")).strip()
        events.append(
            CalendarEvent(
                timestamp=timestamp,
                currency=currency,
                event_name=event_name,
                country=country,
                importance=_normalize_impact(lower.get("importance") or lower.get("impact")),
                categories=tuple(tag_categories(event_name)),
                previous=parse_number(lower.get("previous")),
                forecast=forecast,
                actual=actual,
                surprise=surprise,
                timestamp_precision_minutes=precision,
                source_local_date=local_date,
            )
        )
    if impute_schedule:
        events = impute_release_times(events)
    return standardize_surprises(events)


# Option A (RESEARCH_TRACKER §6, 2026-10-24, pre-registered before any H1-H5 or R1 re-run).
# Pandemic-era surprises (e.g. payrolls +10.3M on 2020-06-05, 14σ under a full-sample σ)
# inflate σ so ordinary releases collapse toward 0σ. σ is therefore estimated from releases
# *outside* this window and applied to every release, including those inside it. The
# robustness variant drops in-window releases. The window is fixed and must not be moved.
PANDEMIC_WINDOW_START = date(2020, 3, 1)
PANDEMIC_WINDOW_END = date(2021, 6, 30)   # inclusive


def in_pandemic_window(event: CalendarEvent) -> bool:
    """Window membership by UTC release date; date-only rows use their source date, since a
    placeholder's UTC timestamp can fall on the previous day."""
    day = event.timestamp.astimezone(UTC).date() if event.has_exact_time else (
        event.source_local_date or event.timestamp.date())
    return PANDEMIC_WINDOW_START <= day <= PANDEMIC_WINDOW_END


def standardize_surprises(events: list[CalendarEvent]) -> list[CalendarEvent]:
    """Per (currency, event_name): z-score the surprise by its historical std (ABDV 2003),
    with σ estimated outside the pandemic window (Option A) and applied to all releases."""
    groups: dict[tuple[str, str], list[float]] = defaultdict(list)
    for event in events:
        if event.surprise is not None and not in_pandemic_window(event):
            groups[(event.currency, event.event_name)].append(event.surprise)

    std_by_group: dict[tuple[str, str], float] = {}
    for key, values in groups.items():
        if len(values) < 2:
            continue
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
        if variance > 0:
            std_by_group[key] = variance ** 0.5

    standardized: list[CalendarEvent] = []
    from dataclasses import replace
    for event in events:
        std = std_by_group.get((event.currency, event.event_name))
        z = (event.surprise / std) if (event.surprise is not None and std) else None
        standardized.append(replace(event, standardized_surprise=z,
                                    in_pandemic_window=in_pandemic_window(event)))
    return standardized


def load_calendar_csv(
    path: str | Path,
    *,
    tz_offset_hours: float = 0.0,
    source_zone: str | None = None,
    impute_schedule: bool = False,
) -> list[CalendarEvent]:
    text = Path(path).read_text(encoding="utf-8")
    reader = csv.DictReader(io.StringIO(text))
    return parse_calendar_records(list(reader), tz_offset_hours=tz_offset_hours,
                                  source_zone=source_zone, impute_schedule=impute_schedule)


def calendar_to_raw_events(events: list[CalendarEvent]) -> list[RawEvent]:
    return [event.to_raw_event() for event in events]

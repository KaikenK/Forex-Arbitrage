"""Reaction training rows from scheduled US macro releases and 1-minute FX bars.

Why this exists. The live model was trained on USD/INR rows only, so for the majors it
extrapolates, and under the keyword sentiment backend it predicted "neutral" for every live
headline. The research calendar store (`research/artifacts/calendar_events.parquet`) holds
~1,500 US high-impact releases with an exact release time and a pre-release consensus, and
the HistData cache holds 1-minute bars for EUR/USD, GBP/USD, USD/JPY and USD/CHF. Together
they give thousands of real, labelled reactions.

Labels (fixed here, mirroring the research event study):
  - Entry = first bar at/after the release, at most 2 min late; exit = first bar at/after
    entry + horizon, at most 5 min late. Bars are stamped at bar *close*, so the entry is the
    last pre-release price. A horizon with no qualifying exit (weekend, data gap) is dropped.
  - `reaction_label` is the **dollar's** return: the pair return times +1 when USD is the
    base (USD/JPY) and -1 when it is the quote (EUR/USD). The model's features are additive
    hashed tokens, so with pair-own labels the same headline would have to push EUR/USD down
    and USD/JPY up, and the text weights would cancel. The artifact records
    `direction_orientation="usd"` and the scorer flips direction back per pair.
  - `magnitude_label` = |pair return|.

Features, so that training rows match what the live service sees:
  - Each release is emitted in two *views*. The "live" view is exactly what the Arbex bridge
    sends for a feed headline: the headline text, `event_category="macro_data"` (the live
    feed's category for data releases), source RSS, no scheduled flag, no numbers. The
    "calendar" view adds what a calendar-driven call can send: scheduled=True, actual,
    forecast, previous and the standardized surprise. Both views of a release share a
    `release_id`, so a split by release keeps them together.
  - The headline is a template (a limitation): "US Non-Farm Employment Change beats
    expectations", with a few deterministic paraphrases per sign. The phrase follows the
    economic sign of the surprise for the dollar — "better/beats/tops" vs "worse/misses"
    (or "hotter/cooler" for inflation) — so the wording carries the sign the model can
    learn from live headlines.
  - The surprise is the research Option A standardized surprise (σ estimated outside the
    2020-03..2021-06 pandemic window), winsorized at ±5σ.
"""
from __future__ import annotations

import bisect
import hashlib
import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

STUDY_PAIRS: tuple[str, ...] = ("EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF")
DEFAULT_HORIZONS: tuple[int, ...] = (60, 240, 1440)
MAX_ENTRY_LAG = timedelta(minutes=2)
MAX_EXIT_LAG = timedelta(minutes=5)
WINSOR_SIGMA = 5.0
IN_LINE_SIGMA = 0.2
LIVE_FEED_CATEGORY = "macro_data"
VIEW_LIVE = "live"
VIEW_CALENDAR = "calendar"
VIEWS: tuple[str, ...] = (VIEW_LIVE, VIEW_CALENDAR)
LABEL_ORIENTATION = "usd"

# Indicator -> the macro_event_type vocabulary the existing USD/INR rows use, so the two
# datasets share event-family tokens. Indicators without a counterpart get None and fall
# back to the category token.
_MACRO_EVENT_TYPES: dict[str, str] = {
    "Non-Farm Employment Change": "nfp",
    "Unemployment Rate": "unemployment",
    "Unemployment Claims": "unemployment",
    "CPI m/m": "cpi",
    "CPI y/y": "cpi",
    "Core CPI m/m": "core-cpi",
    "Advance GDP q/q": "gdp",
    "Prelim GDP q/q": "gdp",
    "Final GDP q/q": "gdp",
    "Federal Funds Rate": "rate-decision",
}
_INFLATION_MARKERS = ("CPI", "PPI", "PCE", "Price Index", "Inflation")


@dataclass(frozen=True)
class CalendarRelease:
    """One scheduled release, as read from the research calendar store."""
    release_id: str
    timestamp: datetime              # UTC release time (exact)
    event_name: str
    country: str
    actual: float | None
    forecast: float | None
    previous: float | None
    standardized_surprise: float     # Option A, before winsorization
    news_sign: int                   # +1: a higher print is good for the currency; -1: bad
    timestamp_source: str = "source"
    in_pandemic_window: bool = False

    @property
    def winsorized_surprise(self) -> float:
        return max(-WINSOR_SIGMA, min(WINSOR_SIGMA, self.standardized_surprise))

    @property
    def good_news_surprise(self) -> float:
        """Surprise oriented so that positive = good for the currency."""
        return self.winsorized_surprise * self.news_sign


class MinuteBars:
    """Bar-close timestamps (UTC, ascending) and closes, with an at-or-after lookup."""

    def __init__(self, times: list[datetime], prices: list[float]) -> None:
        if len(times) != len(prices):
            raise ValueError("times and prices must have the same length.")
        if any(later < earlier for earlier, later in zip(times, times[1:])):
            raise ValueError("bar times must be ascending.")
        self._times = times
        self._prices = prices

    def at_or_after(self, moment: datetime) -> tuple[datetime, float] | None:
        index = bisect.bisect_left(self._times, moment)
        if index >= len(self._times):
            return None
        return self._times[index], self._prices[index]


def usd_side(pair: str) -> int:
    base, _, quote = pair.strip().upper().partition("/")
    if base == "USD":
        return 1
    if quote == "USD":
        return -1
    return 0


def macro_event_type_for(event_name: str) -> str | None:
    return _MACRO_EVENT_TYPES.get(event_name)


def release_headline(release: CalendarRelease) -> str:
    """The templated headline a live feed would carry for this release."""
    # No printed numbers: each release's values are unique tokens that let a hashed linear
    # model memorise individual releases (and collide with the words that carry the sign).
    # The calendar view passes the numbers as structured fields instead.
    #
    # Phrasing varies deterministically per row (as real wire headlines do). With a single
    # template, tokens such as "US", "than" and "expected" appeared in every calendar row
    # and in few real headlines, so the model used them as a dataset indicator, and a real
    # headline without them inherited the other dataset's bias.
    name = release.event_name
    surprise = release.good_news_surprise
    if abs(surprise) < IN_LINE_SIGMA:
        phrases = _IN_LINE_PHRASES
    elif any(marker in name for marker in _INFLATION_MARKERS):
        phrases = _HOT_PHRASES if surprise > 0 else _COOL_PHRASES
    else:
        phrases = _GOOD_PHRASES if surprise > 0 else _BAD_PHRASES
    digest = int(hashlib.sha256(f"{release.release_id}|{name}".encode()).hexdigest()[:8], 16)
    prefix = "" if digest % 3 == 0 else "US "
    return f"{prefix}{name} {phrases[(digest // 3) % len(phrases)]}"


_GOOD_PHRASES = ("better than expected", "beats expectations", "tops forecasts", "stronger than expected")
_BAD_PHRASES = ("worse than expected", "misses expectations", "falls short of forecasts", "weaker than expected")
_HOT_PHRASES = ("hotter than expected", "rises more than expected", "tops forecasts")
_COOL_PHRASES = ("cooler than expected", "rises less than expected", "softer than expected")
_IN_LINE_PHRASES = ("in line with expectations", "matches forecasts", "as expected")


def surprise_word_direction(headline: str) -> int:
    """The direction a reader would take from the template's phrase alone (a baseline)."""
    text = headline.casefold()
    if any(phrase in text for phrase in _GOOD_PHRASES + _HOT_PHRASES):
        return 1
    if any(phrase in text for phrase in _BAD_PHRASES + _COOL_PHRASES):
        return -1
    return 0


def align_return(
    bars: MinuteBars,
    release_time: datetime,
    horizon_minutes: int,
) -> tuple[datetime, float, datetime, float] | None:
    """(entry_time, entry_price, exit_time, exit_price) under the research lag rules, or None."""
    entry = bars.at_or_after(release_time)
    if entry is None or entry[0] - release_time > MAX_ENTRY_LAG or entry[1] == 0:
        return None
    target = entry[0] + timedelta(minutes=horizon_minutes)
    exit_bar = bars.at_or_after(target)
    if exit_bar is None or exit_bar[0] - target > MAX_EXIT_LAG:
        return None
    return entry[0], entry[1], exit_bar[0], exit_bar[1]


def hours_needed(releases: Iterable[CalendarRelease], horizons: Iterable[int]) -> set[datetime]:
    """UTC hours whose bars an export must load (bar *start* hours, as HistData keys them).

    Bars are stamped at close (start + 1 min), so a lookup window [T, T + lag] needs bars
    whose start falls in [T - 1 min, T + lag]; both ends' hours are included.
    """
    horizons = list(horizons)
    hours: set[datetime] = set()
    for release in releases:
        entry_window = (release.timestamp, release.timestamp + MAX_ENTRY_LAG)
        windows = [entry_window]
        for horizon in horizons:
            start = release.timestamp + timedelta(minutes=horizon)
            windows.append((start, start + MAX_ENTRY_LAG + MAX_EXIT_LAG))
        for low, high in windows:
            for moment in (low - timedelta(minutes=1), high):
                hours.add(moment.astimezone(UTC).replace(minute=0, second=0, microsecond=0))
    return hours


def build_release_rows(
    release: CalendarRelease,
    bars_by_pair: Mapping[str, MinuteBars],
    *,
    horizons: Iterable[int] = DEFAULT_HORIZONS,
    views: Iterable[str] = VIEWS,
    ticker_for: Callable[[str], str] | None = None,
) -> list[dict[str, object]]:
    """Training rows (JSON-ready dicts) for one release across pairs, horizons and views."""
    views = list(views)
    unknown = set(views) - set(VIEWS)
    if unknown:
        raise ValueError(f"unknown views: {sorted(unknown)}")
    headline = release_headline(release)
    rows: list[dict[str, object]] = []
    for pair, bars in bars_by_pair.items():
        side = usd_side(pair)
        if side == 0:
            continue
        for horizon in horizons:
            aligned = align_return(bars, release.timestamp, horizon)
            if aligned is None:
                continue
            entry_time, entry_price, exit_time, exit_price = aligned
            pair_return = exit_price / entry_price - 1.0
            if not math.isfinite(pair_return):
                continue
            for view in views:
                rows.append(_row(
                    release, pair=pair, horizon=horizon, view=view, headline=headline,
                    pair_return=pair_return, usd_return=pair_return * side,
                    entry_time=entry_time, exit_time=exit_time,
                    ticker=ticker_for(pair) if ticker_for else None,
                ))
    return rows


def release_id_for(event_name: str, timestamp: datetime) -> str:
    key = f"US|{event_name}|{timestamp.astimezone(UTC).isoformat()}"
    return "calrel-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _row(
    release: CalendarRelease,
    *,
    pair: str,
    horizon: int,
    view: str,
    headline: str,
    pair_return: float,
    usd_return: float,
    entry_time: datetime,
    exit_time: datetime,
    ticker: str | None,
) -> dict[str, object]:
    calendar_view = view == VIEW_CALENDAR
    metadata: dict[str, str] = {
        # Several indicators can print at one moment (payrolls + unemployment + earnings);
        # they share `release_id` (the split group) but each row keeps its own event id.
        "event_id": f"{release.release_id}:{_slug(release.event_name)}:{pair.replace('/', '')}:{view}",
        "release_id": release.release_id,
        "dataset": "calendar",
        "row_view": view,
        "label_orientation": LABEL_ORIENTATION,
        "source": "CALENDAR" if calendar_view else "RSS",
        "event_category": LIVE_FEED_CATEGORY,
        "indicator": release.event_name,
        "pair_return": repr(pair_return),
        "entry_time": entry_time.isoformat(),
        "exit_time": exit_time.isoformat(),
        "timestamp_source": release.timestamp_source,
        "in_pandemic_window": "true" if release.in_pandemic_window else "false",
        "synthetic_headline": "true",
    }
    if ticker:
        metadata["ticker"] = ticker
    macro_event_type = macro_event_type_for(release.event_name) if calendar_view else None
    if macro_event_type:
        metadata["macro_event_type"] = macro_event_type
    if calendar_view:
        metadata["scheduled"] = "true"
    return {
        "article_text": headline,
        "timestamp": release.timestamp.isoformat(),
        "pair": pair,
        "horizon_minutes": int(horizon),
        "sentiment_label": 0.0,
        "reaction_label": usd_return,
        "magnitude_label": abs(pair_return),
        "source_metadata": metadata,
        "event_category": LIVE_FEED_CATEGORY,
        "macro_event_type": macro_event_type,
        "scheduled": calendar_view,
        "actual": release.actual if calendar_view else None,
        "forecast": release.forecast if calendar_view else None,
        "previous": release.previous if calendar_view else None,
        "standardized_surprise": release.winsorized_surprise if calendar_view else None,
        "calendar_source": "forexfactory_dump" if calendar_view else None,
        "source_url": None,
    }


def _slug(text: str) -> str:
    return "-".join("".join(ch.lower() if ch.isalnum() else " " for ch in text).split())



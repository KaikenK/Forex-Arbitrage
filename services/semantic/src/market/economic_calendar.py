"""Live economic calendar for the risk channel (behind SEMANTIC_CURRENCY_BIAS).

Source: the free ForexFactory weekly JSON (`FF_THIS_WEEK_URL`). Verified on 2026-10-25
against a real response:
  - a list of {title, country, date, impact, forecast, previous}; `country` is the
    currency code (USD, EUR, ...); there is NO `actual` field, so the live event-surprise
    component cannot be computed from this source and stays absent;
  - `date` is ISO-8601 New York wall-clock time with its own UTC offset (-04:00 in the EDT
    week checked). The research dump had a stale-offset bug, so every parse re-localises
    the wall clock in America/New_York and counts disagreements with the embedded offset
    (`offset_mismatches`); USD high-impact rows are checked against the known agency clocks
    (`usd_clock_agreement`) as a running sanity check;
  - `impact` is Low / Medium / High / Holiday. Holiday rows carry an arbitrary clock time
    (an NZ bank holiday was stamped 15:00 New York), so they are time-unknown. A New York
    00:00:00 stamp is treated the same way, the placeholder the research dump used.
Time-unknown rows never produce a release proximity (fail closed).

Fetching: at most once per hour (the endpoint rate-limits hard), atomically cached under
`<cache_root>/calendar/`, stale cache served with its age on failure. Requests never wait on
the network: a daemon thread refreshes the cache, readers only read memory.

Coverage gap: the file covers the current week only, so on Sundays the next week's Monday
releases are unknown until the source rolls over; reported as `coverage_end`.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

FF_THIS_WEEK_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NEW_YORK = ZoneInfo("America/New_York")
MIN_FETCH_INTERVAL_SECONDS = 3600.0
REFRESH_CHECK_SECONDS = 60.0
# Engineering defaults for the live risk channel (H6b evaluates them; not research results).
RELEASE_WINDOW_BEFORE_MINUTES = 15.0
RELEASE_WINDOW_AFTER_MINUTES = 10.0
# Known New York release clocks of US high-impact data (research/data/release_schedule.py,
# plus FOMC statement/press conference); used only as a sanity check on the feed's clock.
US_AGENCY_CLOCKS = frozenset({"08:15", "08:30", "09:45", "10:00", "14:00", "14:30"})
CACHE_FILE = "ff_calendar_thisweek.json"
META_FILE = "ff_calendar_thisweek.meta.json"


@dataclass(frozen=True)
class CalendarRelease:
    utc: str | None            # ISO UTC, None when the time is unknown
    currency: str
    event: str
    impact: str                # low | medium | high | holiday | ""
    forecast: str
    previous: str
    time_known: bool
    date_local: str            # New York calendar date of the row

    @property
    def timestamp(self) -> float | None:
        return None if self.utc is None else datetime.fromisoformat(self.utc).timestamp()


@dataclass(frozen=True)
class ParseReport:
    rows: int
    parsed: int
    time_unknown: int
    offset_mismatches: int
    usd_high_checked: int
    usd_high_on_agency_clock: int

    @property
    def usd_clock_agreement(self) -> float | None:
        return None if not self.usd_high_checked else self.usd_high_on_agency_clock / self.usd_high_checked


def parse_ff_calendar(rows: Iterable[dict]) -> tuple[list[CalendarRelease], ParseReport]:
    releases: list[CalendarRelease] = []
    total = unknown = mismatches = usd_checked = usd_on_clock = 0
    for row in rows:
        total += 1
        try:
            embedded = datetime.fromisoformat(str(row.get("date", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        wall = embedded.replace(tzinfo=None)
        localized = wall.replace(tzinfo=NEW_YORK)
        if embedded.tzinfo is not None and embedded.utcoffset() != localized.utcoffset():
            mismatches += 1
        impact = str(row.get("impact") or "").strip().lower()
        currency = str(row.get("country") or "").strip().upper()
        placeholder = (wall.hour, wall.minute, wall.second) == (0, 0, 0)
        time_known = impact in {"low", "medium", "high"} and not placeholder
        if not time_known:
            unknown += 1
        elif currency == "USD" and impact == "high":
            usd_checked += 1
            usd_on_clock += wall.strftime("%H:%M") in US_AGENCY_CLOCKS
        releases.append(CalendarRelease(
            utc=localized.astimezone(UTC).isoformat() if time_known else None,
            currency=currency, event=str(row.get("title") or "").strip(), impact=impact,
            forecast=str(row.get("forecast") or ""), previous=str(row.get("previous") or ""),
            time_known=time_known, date_local=wall.date().isoformat()))
    releases.sort(key=lambda r: (r.timestamp is None, r.timestamp or 0.0))
    return releases, ParseReport(total, len(releases), unknown, mismatches, usd_checked, usd_on_clock)


def _http_get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (arbex-semantic)"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


class EconomicCalendar:
    """Cached weekly calendar. Readers never touch the network."""

    def __init__(self, cache_dir: Path, *, fetch: Callable[[str], bytes] | None = None,
                 clock: Callable[[], float] | None = None, url: str = FF_THIS_WEEK_URL) -> None:
        self._dir = Path(cache_dir)
        self._fetch = fetch or _http_get
        self._clock = clock or time.time
        self._url = url
        self._lock = threading.Lock()
        self._releases: list[CalendarRelease] = []
        self._report: ParseReport | None = None
        self._fetched_at: float | None = None
        self._last_attempt: float | None = None
        self._last_error: str | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._load_cache()

    # ------------------------------------------------------------ cache + fetch
    def _load_cache(self) -> None:
        data_path, meta_path = self._dir / CACHE_FILE, self._dir / META_FILE
        try:
            rows = json.loads(data_path.read_text(encoding="utf-8"))
            meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        except (OSError, ValueError):
            return
        self._install(rows, float(meta.get("fetched_at") or data_path.stat().st_mtime))

    def _install(self, rows: list, fetched_at: float) -> None:
        releases, report = parse_ff_calendar(rows)
        with self._lock:
            self._releases, self._report, self._fetched_at = releases, report, fetched_at

    def refresh(self, *, force: bool = False) -> bool:
        """Fetch once if the hourly budget allows. Returns True if new data was installed."""
        now = self._clock()
        if not force and self._last_attempt is not None and now - self._last_attempt < MIN_FETCH_INTERVAL_SECONDS:
            return False
        if not force and self._fetched_at is not None and now - self._fetched_at < MIN_FETCH_INTERVAL_SECONDS:
            return False
        self._last_attempt = now
        try:
            raw = self._fetch(self._url)
            rows = json.loads(raw.decode("utf-8"))
            if not isinstance(rows, list):
                raise ValueError("calendar response is not a list")
        except Exception as exc:                       # keep serving the stale cache
            self._last_error = f"{type(exc).__name__}: {exc}"[:300]
            return False
        self._write_atomic(raw, now)
        self._install(rows, now)
        self._last_error = None
        return True

    def _write_atomic(self, raw: bytes, fetched_at: float) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        for name, payload in ((CACHE_FILE, raw), (META_FILE, json.dumps({"fetched_at": fetched_at,
                                                                         "url": self._url}).encode("utf-8"))):
            target = self._dir / name
            temp = target.with_name(f"{name}.{os.getpid()}.{threading.get_ident()}.tmp")
            try:
                temp.write_bytes(payload)
                os.replace(temp, target)
            finally:
                if temp.exists():
                    temp.unlink()

    def start(self) -> None:
        """Background refresher (daemon). Safe to call once at service startup."""
        if self._thread is not None:
            return

        def loop() -> None:
            while not self._stop.is_set():
                self.refresh()
                self._stop.wait(REFRESH_CHECK_SECONDS)

        self._thread = threading.Thread(target=loop, name="economic-calendar", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------ queries
    def status(self) -> dict[str, object]:
        with self._lock:
            report, fetched_at, releases = self._report, self._fetched_at, self._releases
        known = [r.timestamp for r in releases if r.timestamp is not None]
        age = None if fetched_at is None else round(self._clock() - fetched_at, 1)
        state = "unavailable" if fetched_at is None else (
            "stale" if age is not None and age > 2 * MIN_FETCH_INTERVAL_SECONDS else "live")
        return {
            "source": self._url, "state": state, "age_seconds": age, "last_error": self._last_error,
            "rows": None if report is None else report.rows,
            "time_unknown_rows": None if report is None else report.time_unknown,
            "offset_mismatches": None if report is None else report.offset_mismatches,
            "usd_clock_agreement": None if report is None else report.usd_clock_agreement,
            "coverage_end": None if not known else datetime.fromtimestamp(max(known), UTC).isoformat(),
            "has_actuals": False,
        }

    def upcoming(self, currencies: Iterable[str] | None = None, hours: float = 24.0,
                 *, include_unknown: bool = False) -> list[dict[str, object]]:
        now = self._clock()
        wanted = {c.upper() for c in currencies} if currencies else None
        with self._lock:
            releases = list(self._releases)
        out = []
        for release in releases:
            if wanted is not None and release.currency not in wanted:
                continue
            ts = release.timestamp
            if ts is None:
                if include_unknown:
                    out.append({**asdict(release), "minutes_from_now": None})
                continue
            if now <= ts <= now + hours * 3600:
                out.append({**asdict(release), "minutes_from_now": round((ts - now) / 60.0, 1)})
        return out

    def proximity(self, currencies: Iterable[str]) -> dict[str, object]:
        """Next / last high-impact, time-known release of any of `currencies`, and whether
        one falls inside the risk window (RELEASE_WINDOW_BEFORE/AFTER_MINUTES)."""
        now = self._clock()
        wanted = {c.upper() for c in currencies}
        with self._lock:
            highs = [r for r in self._releases if r.impact == "high" and r.timestamp is not None
                     and r.currency in wanted]
        nxt = min((r for r in highs if r.timestamp >= now), key=lambda r: r.timestamp, default=None)
        last = max((r for r in highs if r.timestamp < now), key=lambda r: r.timestamp, default=None)
        to_next = None if nxt is None else round((nxt.timestamp - now) / 60.0, 1)
        since_last = None if last is None else round((now - last.timestamp) / 60.0, 1)
        in_window = None
        if to_next is not None and to_next <= RELEASE_WINDOW_BEFORE_MINUTES:
            in_window = {"phase": "pre_release", "minutes": to_next, "currency": nxt.currency, "event": nxt.event}
        elif since_last is not None and since_last <= RELEASE_WINDOW_AFTER_MINUTES:
            in_window = {"phase": "post_release", "minutes": since_last, "currency": last.currency, "event": last.event}
        return {
            "minutes_to_next": to_next, "next_event": None if nxt is None else f"{nxt.currency} {nxt.event}",
            "minutes_since_last": since_last, "last_event": None if last is None else f"{last.currency} {last.event}",
            "in_window": in_window,
            "window_minutes": {"before": RELEASE_WINDOW_BEFORE_MINUTES, "after": RELEASE_WINDOW_AFTER_MINUTES},
        }


def calendar_cache_dir() -> Path:
    from retrieval.cache_paths import cache_root

    return cache_root() / "calendar"

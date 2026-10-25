"""GDELT DOC 2.0 connector — free, no key, real headlines + timestamps back to 2017.

Fetches article headlines matching an FX-relevant query over a date range, tags each with
affected currencies + event categories, and returns `RawEvent`s. Time is chunked into windows
because the API caps at ~250 records per call; historical backfill walks windows across years.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta

from .base import RawEvent
from .tagging import tag_categories, tag_currencies

DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"

# Broad FX-relevant query. GDELT ORs terms; sourcelang keeps it English for the NLP stage.
DEFAULT_QUERY = (
    '(forex OR "exchange rate" OR "central bank" OR "Federal Reserve" OR "rate decision" '
    'OR inflation OR "interest rate" OR ECB OR "Bank of England" OR "Bank of Japan" '
    'OR "US dollar" OR euro OR "Indian rupee") sourcelang:eng'
)

_MOBILE_KEYS = ("url", "title", "seendate", "domain", "sourcecountry", "language")


def _parse_seendate(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def parse_gdelt_articles(payload: dict) -> list[RawEvent]:
    """Convert a GDELT DOC ArtList JSON payload into tagged RawEvents (skips untaggable)."""
    articles = payload.get("articles")
    if not isinstance(articles, list):
        return []
    events: list[RawEvent] = []
    for article in articles:
        if not isinstance(article, dict):
            continue
        title = str(article.get("title", "")).strip()
        url = str(article.get("url", "")).strip()
        timestamp = _parse_seendate(str(article.get("seendate", "")))
        if not title or not url or timestamp is None:
            continue
        currencies = tag_currencies(title)
        categories = tag_categories(title)
        if not currencies:  # cannot attribute to a pair -> not useful for the study
            continue
        events.append(
            RawEvent(
                event_id=hashlib.sha256(url.encode("utf-8")).hexdigest()[:16],
                timestamp=timestamp,
                headline=title,
                source="gdelt",
                url=url,
                domain=str(article.get("domain", "")),
                sourcecountry=str(article.get("sourcecountry", "")),
                language=str(article.get("language", "")),
                currencies=tuple(currencies),
                categories=tuple(categories),
                scheduled=False,
            )
        )
    return events


class GdeltRateLimited(RuntimeError):
    """GDELT refused the request without an HTTP error code.

    The DOC API often answers a too-fast client with HTTP 200 and a plain-text notice
    ("Please limit requests to one every 5 seconds ...") instead of JSON. Treating that body
    as "no articles" silently records a real news day as empty, so it is raised as throttling.
    """


def _request(query: str, start: datetime, end: datetime, max_records: int) -> urllib.request.Request:
    params = urllib.parse.urlencode({
        "query": query,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": str(max_records),
        "startdatetime": start.strftime("%Y%m%d%H%M%S"),
        "enddatetime": end.strftime("%Y%m%d%H%M%S"),
        "sort": "datedesc",
    })
    return urllib.request.Request(f"{DOC_API}?{params}", headers={"user-agent": "fx-research/0.1"})


class GdeltMalformedPayload(ValueError):
    """A JSON-looking body that cannot be parsed even after repair. Not throttling: the window
    is recorded as failed (no sentinel), so a later run retries it."""


# Backslashes GDELT leaves unescaped inside titles/URLs (e.g. "\'"), which strict JSON rejects.
_INVALID_ESCAPE = re.compile(r'\\(?!["\\/bfnrtu])')


def decode_payload(body: str) -> dict:
    """Parse a DOC API body.

    - empty body -> a genuine empty window;
    - a body that is not JSON at all -> a refusal (`GdeltRateLimited`), never an empty window;
    - JSON with raw control characters or invalid escapes inside titles (the 2026-10-24 pilot
      hit these) -> parsed leniently, because the articles in it are real. Earlier code
      silently recorded such windows as empty.
    """
    text = body.strip()
    if not text:
        return {"articles": []}
    if not text.startswith("{"):
        raise GdeltRateLimited(text[:160])
    for candidate in (text, _INVALID_ESCAPE.sub(r"\\\\", text)):
        try:
            payload = json.loads(candidate, strict=False)
        except json.JSONDecodeError:
            continue
        return payload if isinstance(payload, dict) else {"articles": []}
    raise GdeltMalformedPayload(text[:160])


def fetch_window_once(query: str, start: datetime, end: datetime, max_records: int) -> list[RawEvent]:
    """One request, no retries: failures propagate so the caller's backoff can classify them."""
    with urllib.request.urlopen(_request(query, start, end, max_records), timeout=60) as response:
        body = response.read().decode("utf-8", "replace")
    return parse_gdelt_articles(decode_payload(body))


def _fetch_window(
    query: str,
    start: datetime,
    end: datetime,
    max_records: int,
    *,
    retries: int = 5,
    backoff_seconds: float = 6.0,
) -> list[RawEvent]:
    """Sequential fetch with simple linear backoff (used by `fetch_gdelt` / build_event_db).
    The backfill uses `fetch_window_once` with shared adaptive pacing instead."""
    for attempt in range(retries):
        try:
            return fetch_window_once(query, start, end, max_records)
        except (urllib.error.HTTPError, GdeltRateLimited) as exc:
            throttled = isinstance(exc, GdeltRateLimited) or exc.code == 429
            if throttled and attempt < retries - 1:
                time.sleep(backoff_seconds * (attempt + 1))
                continue
            raise
    return []


def fetch_gdelt(
    start: datetime,
    end: datetime,
    *,
    query: str = DEFAULT_QUERY,
    window_hours: int = 24,
    max_records: int = 250,
    sleep_seconds: float = 1.0,
) -> list[RawEvent]:
    """Walk [start, end) in windows, deduplicating by event_id across windows."""
    seen: set[str] = set()
    collected: list[RawEvent] = []
    cursor = start
    step = timedelta(hours=window_hours)
    while cursor < end:
        window_end = min(cursor + step, end)
        try:
            window_events = _fetch_window(query, cursor, window_end, max_records)
        except Exception as exc:  # one bad window must not abort a multi-year backfill
            print(f"  window {cursor.date()} failed: {exc}")
            window_events = []
        for event in window_events:
            if event.event_id in seen:
                continue
            seen.add(event.event_id)
            collected.append(event)
        cursor = window_end
        if cursor < end:
            time.sleep(sleep_seconds)  # be polite to the free endpoint
    return collected

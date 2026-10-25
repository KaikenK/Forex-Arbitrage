"""Load news_stream timestamped-event JSONL into event-study `Event` objects.

Expected fields (news_stream schema): published_at (or timestamp), symbols, event_category,
macro_event_type, standardized_surprise, scheduled, headline, source.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from research.eventstudy.schema import Event


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _categories(row: dict) -> tuple[str, ...]:
    categories: list[str] = []
    for key in ("event_category", "macro_event_type"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            label = value.strip().lower()
            if label not in categories:
                categories.append(label)
    return tuple(categories)


def _surprise(row: dict) -> float | None:
    value = row.get("standardized_surprise")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def load_events(path: str | Path, *, default_pair: str = "USD/INR") -> list[Event]:
    events: list[Event] = []
    for index, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        timestamp = _parse_ts(row.get("published_at") or row.get("timestamp"))
        if timestamp is None:
            continue
        symbols = row.get("symbols")
        pair = str(symbols[0]) if isinstance(symbols, list) and symbols else default_pair
        events.append(
            Event(
                event_id=str(row.get("event_id") or f"event-{index}"),
                timestamp=timestamp,
                pair=pair,
                categories=_categories(row),
                surprise=_surprise(row),
                source=str(row.get("source", "")),
                headline=str(row.get("headline", "")),
            )
        )
    return events

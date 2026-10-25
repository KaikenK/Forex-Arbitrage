"""Connector framework + append-only event store for the scaled event database.

`RawEvent` is the rich, source-agnostic row (multi-currency, multi-category). It converts to
one event-study `Event` per affected pair for alignment. `EventStore` is an append-only Parquet
table deduplicated by `event_id`, with a JSON manifest recording every fetch for reproducibility.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from research.eventstudy.schema import Event


@dataclass(frozen=True)
class RawEvent:
    event_id: str
    timestamp: datetime            # UTC publication time
    headline: str
    source: str                    # provider, e.g. "gdelt", "fred", "forexfactory"
    url: str | None = None
    domain: str = ""
    sourcecountry: str = ""
    language: str = ""
    currencies: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    surprise: float | None = None
    scheduled: bool = False
    # "scheduled" (calendar releases, used for the event study) vs "unscheduled" (news).
    event_type: str = "unscheduled"
    # Calendar-only fields (None for unscheduled news):
    country: str = ""
    event_name: str = ""
    importance: str = ""
    previous: float | None = None
    forecast: float | None = None
    actual: float | None = None
    standardized_surprise: float | None = None
    # How precisely `timestamp` is known, in minutes (0 = exact). GDELT crawl times are
    # quantized to 15-minute buckets; date-only calendar rows are 1440.
    timestamp_precision_minutes: float = 0.0
    # "source" = observed; "schedule" = a missing calendar time filled from the agency clock.
    timestamp_source: str = "source"
    # Calendar releases inside the pre-registered pandemic window (Option A robustness).
    in_pandemic_window: bool = False

    def to_row(self) -> dict:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "headline": self.headline,
            "source": self.source,
            "event_type": self.event_type,
            "url": self.url,
            "domain": self.domain,
            "sourcecountry": self.sourcecountry,
            "language": self.language,
            "currencies": ";".join(self.currencies),
            "categories": ";".join(self.categories),
            "country": self.country,
            "event_name": self.event_name,
            "importance": self.importance,
            "previous": self.previous,
            "forecast": self.forecast,
            "actual": self.actual,
            "surprise": self.surprise,
            "standardized_surprise": self.standardized_surprise,
            "scheduled": self.scheduled,
            "timestamp_precision_minutes": self.timestamp_precision_minutes,
            "timestamp_source": self.timestamp_source,
            "in_pandemic_window": self.in_pandemic_window,
        }

    def to_study_events(self) -> list[Event]:
        from .tagging import currencies_to_pairs

        events: list[Event] = []
        for pair in currencies_to_pairs(list(self.currencies)):
            events.append(
                Event(
                    event_id=f"{self.event_id}:{pair.replace('/', '')}",
                    timestamp=self.timestamp,
                    pair=pair,
                    categories=self.categories,
                    surprise=self.surprise,
                    source=self.source,
                    headline=self.headline,
                    timestamp_precision_minutes=self.timestamp_precision_minutes,
                )
            )
        return events


class EventStore:
    """Append-only Parquet event table, deduplicated by event_id, with a fetch manifest."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.manifest_path = self.path.with_suffix(".manifest.json")

    def append(self, events: list[RawEvent], *, fetch_meta: dict | None = None) -> int:
        if not events:
            return 0
        frame_new = pd.DataFrame([event.to_row() for event in events])
        frame_new = frame_new.drop_duplicates(subset="event_id")

        if self.path.exists():
            frame_old = pd.read_parquet(self.path)
            frame_new = frame_new[~frame_new["event_id"].isin(set(frame_old["event_id"]))]
            frame = pd.concat([frame_old, frame_new], ignore_index=True)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            frame = frame_new

        frame.to_parquet(self.path, index=False)
        self._record_manifest(added=len(frame_new), total=len(frame), fetch_meta=fetch_meta or {})
        return len(frame_new)

    def load(self) -> pd.DataFrame:
        if not self.path.exists():
            return pd.DataFrame()
        return pd.read_parquet(self.path)

    def _record_manifest(self, *, added: int, total: int, fetch_meta: dict) -> None:
        entries: list[dict] = []
        if self.manifest_path.exists():
            try:
                entries = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                entries = []
        entries.append({
            "fetched_at": datetime.now(UTC).isoformat(),
            "added": added,
            "total": total,
            **fetch_meta,
        })
        self.manifest_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")

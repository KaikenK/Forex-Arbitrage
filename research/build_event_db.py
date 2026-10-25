"""Backfill the scaled event database from GDELT.

Examples:
  python -m research.build_event_db --start 2024-01-01 --end 2024-01-08
  python -m research.build_event_db --start 2018-01-01 --end 2025-12-31 --window-hours 24
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from research.data.base import EventStore
from research.data.gdelt import DEFAULT_QUERY, fetch_gdelt

DEFAULT_STORE = Path(__file__).resolve().parent / "artifacts" / "events.parquet"


def _parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill the FX event database from GDELT.")
    parser.add_argument("--start", type=_parse_date, required=True)
    parser.add_argument("--end", type=_parse_date, required=True)
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--window-hours", type=int, default=24)
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--sleep", type=float, default=1.0)
    args = parser.parse_args()

    print(f"Fetching GDELT {args.start.date()} .. {args.end.date()} (window={args.window_hours}h)")
    events = fetch_gdelt(
        args.start, args.end,
        query=args.query, window_hours=args.window_hours, sleep_seconds=args.sleep,
    )
    print(f"Fetched {len(events)} tagged events")

    store = EventStore(args.store)
    added = store.append(events, fetch_meta={
        "source": "gdelt",
        "start": args.start.isoformat(),
        "end": args.end.isoformat(),
        "query": args.query,
    })
    frame = store.load()
    print(f"Added {added} new (store total: {len(frame)}) -> {args.store}")
    if not frame.empty:
        exploded = frame["categories"].str.split(";").explode()
        print("\nTop categories:")
        print(exploded[exploded != ""].value_counts().head(12).to_string())
        cur = frame["currencies"].str.split(";").explode()
        print("\nTop currencies:")
        print(cur[cur != ""].value_counts().head(12).to_string())


if __name__ == "__main__":
    main()

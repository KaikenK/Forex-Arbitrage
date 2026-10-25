"""Ingest a static economic-calendar CSV into the event store as scheduled events.

Scheduled releases are the causal arm of the study: their timestamps are exact (unlike
GDELT's 15-minute buckets) and they carry a pre-release consensus, so `actual - forecast`
is an exogenous surprise rather than a post-hoc rationalisation.

Timestamps (RESEARCH_TRACKER §6, 2026-10-24): the dump's wall clock is re-localised in
Asia/Tehran with current tzdata, local 00:00:00 is treated as "time missing", and missing
times of validated US release families are filled from the agency clock. Event ids hash
the timestamp, so a store built before that fix must be rebuilt with --replace, not appended.

Run:
  python -m research.ingest_calendar --replace
  python -m research.ingest_calendar --csv <path> --source-zone none --no-impute
"""
from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd

from research.data.base import EventStore
from research.data.calendar import (
    FOREXFACTORY_SOURCE_ZONE,
    PANDEMIC_WINDOW_END,
    PANDEMIC_WINDOW_START,
    calendar_to_raw_events,
    parse_calendar_records,
    standardize_surprises,
)
from research.data.release_schedule import impute_release_times, validate_release_clocks

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
DEFAULT_CSV = ARTIFACTS / "forex_factory_calendar.csv"
DEFAULT_STORE = ARTIFACTS / "calendar_events.parquet"


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest an economic-calendar CSV.")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--min-year", type=int, default=2018)
    parser.add_argument("--max-year", type=int, default=2026)
    parser.add_argument("--require-exact-time", action="store_true",
                        help="Drop date-only rows (time missing) that have no release time.")
    parser.add_argument("--require-surprise", action="store_true",
                        help="Keep only rows with both actual and forecast present.")
    parser.add_argument("--source-zone", default=FOREXFACTORY_SOURCE_ZONE,
                        help="IANA zone of the dump's wall clock; 'none' trusts embedded offsets.")
    parser.add_argument("--no-impute", action="store_true",
                        help="Do not fill missing times from documented release clocks.")
    parser.add_argument("--replace", action="store_true",
                        help="Back up the existing store and rebuild it instead of appending.")
    args = parser.parse_args()

    print(f"Reading {args.csv}")
    frame = pd.read_csv(args.csv, low_memory=False)
    print(f"  {len(frame)} raw rows | columns: {list(frame.columns)}")

    records = frame.to_dict("records")
    zone = None if args.source_zone.lower() == "none" else args.source_zone
    events = parse_calendar_records(records, source_zone=zone)
    print(f"  parsed {len(events)} calendar events (wall clock zone: {zone or 'embedded offsets'})")

    checks = validate_release_clocks(events)
    print("\nRelease-clock validation (observed times vs documented clock):")
    for check in checks.values():
        verdict = "IMPUTE" if check.validated and not args.no_impute else "keep date-only"
        print(f"  {check.family:<34} {check.clock}  agree {check.agreeing_rows:>4}/{check.timed_rows:<4}"
              f" ({check.agreement:.1%})  missing {check.missing_rows:>4}  -> {verdict}")
    if not args.no_impute:
        # Re-standardize so pandemic-window membership uses the filled release times.
        events = standardize_surprises(impute_release_times(events, checks))
    imputed = sum(e.timestamp_source == "schedule" for e in events)
    print(f"  {imputed} missing times filled from release clocks\n")

    kept = [e for e in events if args.min_year <= e.timestamp.year <= args.max_year]
    print(f"  {len(kept)} within {args.min_year}-{args.max_year}")

    exact = [e for e in kept if e.has_exact_time]
    print(f"  {len(exact)} with an exact release time "
          f"({len(kept) - len(exact)} date-only rows with no usable time)")
    if args.require_exact_time:
        kept = exact

    with_surprise = [e for e in kept if e.surprise is not None]
    print(f"  {len(with_surprise)} with a computable surprise (actual and forecast present)")
    if args.require_surprise:
        kept = with_surprise

    standardized = [e for e in kept if e.standardized_surprise is not None]
    in_window = sum(e.in_pandemic_window for e in standardized)
    print(f"  sigma estimated outside {PANDEMIC_WINDOW_START}..{PANDEMIC_WINDOW_END} (Option A); "
          f"{in_window} standardized releases fall inside the window")
    print(f"  {len(standardized)} with a standardized surprise (>=2 observations of that event)")

    if not kept:
        print("Nothing to ingest.")
        return

    store = EventStore(args.store)
    if args.replace:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        for path in (store.path, store.manifest_path):
            if path.exists():
                backup = path.with_name(f"{path.name}.bak-{stamp}")
                shutil.move(path, backup)
                print(f"  backed up {path.name} -> {backup.name}")
    added = store.append(
        calendar_to_raw_events(kept),
        fetch_meta={"source": "calendar_csv", "path": str(args.csv), "rows": len(frame)},
    )
    stored = store.load()
    print(f"\nAdded {added} new (store total {len(stored)}) -> {args.store}")

    print("\nBy currency:")
    print(stored["currencies"].value_counts().head(10).to_string())
    print("\nBy importance:")
    print(stored["importance"].value_counts().to_string())
    top = stored[stored["importance"] == "high"]["event_name"].value_counts().head(8)
    if not top.empty:
        print("\nMost frequent high-impact events:")
        print(top.to_string())


if __name__ == "__main__":
    main()

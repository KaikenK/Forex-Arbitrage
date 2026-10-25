"""Import high-impact USD/INR macro event seeds from public calendar sources."""
from __future__ import annotations

import argparse
from datetime import date
import json
import site
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
USER_SITE_PATH = Path(site.getusersitepackages())
for import_path in (str(USER_SITE_PATH), str(SRC_PATH)):
    if import_path not in sys.path:
        sys.path.insert(0, import_path)

from training.calendar_import import DEFAULT_MIN_IMPORTANCE
from training.calendar_import import convert_calendar_rows_to_seeds
from training.calendar_import import fetch_usdinr_calendar_rows
from training.calendar_import import load_seed_records
from training.calendar_import import merge_seed_records
from training.calendar_import import summarize_seed_records
from training.calendar_import import write_seed_records

DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "data" / "usdinr_timestamped_events_imported.jsonl"
DEFAULT_EXISTING_PATH = PROJECT_ROOT / "data" / "usdinr_timestamped_events.jsonl"
DEFAULT_MERGED_OUTPUT_PATH = PROJECT_ROOT / "data" / "usdinr_timestamped_events_expanded.jsonl"


def main() -> None:
    args = _parse_args()
    print(
        f"Step 1: Fetching calendar rows from {args.start_date.isoformat()} to {args.end_date.isoformat()} "
        f"(importance>={args.min_importance})..."
    )
    raw_rows = fetch_usdinr_calendar_rows(
        args.start_date,
        args.end_date,
        min_importance=args.min_importance,
    )
    print(f"  -> {len(raw_rows)} raw calendar rows")

    print("Step 2: Converting calendar rows into timestamped event seeds...")
    imported_records = convert_calendar_rows_to_seeds(raw_rows)
    write_seed_records(args.output_path, imported_records)
    imported_summary = summarize_seed_records(imported_records)
    print(f"  -> wrote {imported_summary['records']} imported seeds to {args.output_path}")
    print(f"  -> macro event types: {json.dumps(imported_summary['macro_event_types'], sort_keys=True)}")
    print(f"  -> countries: {json.dumps(imported_summary['countries'], sort_keys=True)}")

    if args.merge_existing:
        print(f"Step 3: Merging imported seeds with existing ledger {args.merge_existing}...")
        existing_records = load_seed_records(args.merge_existing)
        merged_records = merge_seed_records(existing_records, imported_records)
        write_seed_records(args.merged_output_path, merged_records)
        merged_summary = summarize_seed_records(merged_records)
        print(f"  -> wrote {merged_summary['records']} merged seeds to {args.merged_output_path}")
        print(f"  -> merged macro event types: {json.dumps(merged_summary['macro_event_types'], sort_keys=True)}")
        print(f"  -> merged countries: {json.dumps(merged_summary['countries'], sort_keys=True)}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import public macro calendar events relevant to USD/INR research.",
    )
    parser.add_argument("--start-date", type=_parse_date, required=True)
    parser.add_argument("--end-date", type=_parse_date, required=True)
    parser.add_argument("--min-importance", type=int, default=DEFAULT_MIN_IMPORTANCE)
    parser.add_argument("--output-path", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--merge-existing", type=Path, default=DEFAULT_EXISTING_PATH)
    parser.add_argument("--merged-output-path", type=Path, default=DEFAULT_MERGED_OUTPUT_PATH)
    return parser.parse_args()


def _parse_date(raw_value: str) -> date:
    return date.fromisoformat(raw_value)


if __name__ == "__main__":
    main()
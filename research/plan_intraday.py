"""Size the event-windowed intraday fetch before committing to it. Downloads nothing.

Reports how many unique (pair, hour) tick files the causal-arm sample needs, how much the
window dedup saves versus naive per-event fetching, and the projected wall-clock and disk
cost, across several window widths and USD attribution policies.

Run:
  python -m research.plan_intraday
  python -m research.plan_intraday --pre 1 --post 4 --usd-policy all
"""
from __future__ import annotations

import argparse
from datetime import UTC
from pathlib import Path

import pandas as pd

from research.data.event_windows import estimate_savings, plan_fetch_hours
from research.data.tagging import currencies_to_pairs
from research.eventstudy.schema import Event

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
CALENDAR_STORE = ARTIFACTS / "calendar_events.parquet"

# Measured against the live Dukascopy endpoint: 2 fresh hours took 45.1s including one
# connection reset, so ~10-25s per hour depending on throttling.
SECONDS_PER_HOUR_LOW = 10.0
SECONDS_PER_HOUR_HIGH = 25.0
KB_PER_HOUR = 20.0  # a busy EUR/USD hour compressed to ~24KB


def load_causal_sample(
    store_path: Path,
    *,
    importance: str = "high",
    require_exact_time: bool = True,
    require_surprise: bool = True,
) -> pd.DataFrame:
    frame = pd.read_parquet(store_path)
    if importance:
        frame = frame[frame["importance"] == importance]
    if require_exact_time:
        frame = frame[frame["timestamp_precision_minutes"] == 0]
    if require_surprise:
        frame = frame[frame["standardized_surprise"].notna()]
    return frame


def to_study_events(frame: pd.DataFrame, usd_policy: str) -> list[Event]:
    events: list[Event] = []
    for row in frame.to_dict("records"):
        currencies = [c for c in str(row.get("currencies", "")).split(";") if c]
        timestamp = pd.Timestamp(row["timestamp"]).to_pydatetime()
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        for pair in currencies_to_pairs(currencies, usd_policy=usd_policy):
            events.append(
                Event(
                    event_id=f"{row['event_id']}:{pair.replace('/', '')}",
                    timestamp=timestamp,
                    pair=pair,
                    categories=tuple(c for c in str(row.get("categories", "")).split(";") if c),
                    surprise=row.get("standardized_surprise"),
                    source="calendar",
                    timestamp_precision_minutes=float(row.get("timestamp_precision_minutes", 0.0)),
                )
            )
    return events


def report(events: list[Event], pre_hours: int, post_hours: int, label: str) -> dict:
    plan = plan_fetch_hours(events, pre_hours=pre_hours, post_hours=post_hours)
    savings = estimate_savings(plan, events, pre_hours=pre_hours, post_hours=post_hours)
    hours = plan.total_hours
    low = hours * SECONDS_PER_HOUR_LOW / 3600.0
    high = hours * SECONDS_PER_HOUR_HIGH / 3600.0
    print(f"  {label:<26}{len(events):>8}{hours:>12,}{savings['reduction']:>10.0%}"
          f"{low:>9.1f}h{high:>9.1f}h{hours * KB_PER_HOUR / 1024:>9.0f} MB")
    return {"events": len(events), "hours": hours, **savings}


def main() -> None:
    parser = argparse.ArgumentParser(description="Size the event-windowed intraday fetch.")
    parser.add_argument("--store", type=Path, default=CALENDAR_STORE)
    parser.add_argument("--pre", type=int, default=2)
    parser.add_argument("--post", type=int, default=6)
    parser.add_argument("--usd-policy", choices=["none", "primary", "all", "study"], default="study")
    args = parser.parse_args()

    frame = load_causal_sample(args.store)
    print(f"Causal sample: {len(frame)} calendar events "
          f"(high impact, exact time, standardized surprise)")
    print(f"  {frame['timestamp'].min()} .. {frame['timestamp'].max()}\n")

    header = (f"  {'configuration':<26}{'obs':>8}{'hours':>12}{'dedup':>10}"
              f"{'fast':>10}{'slow':>10}{'disk':>12}")

    print("USD attribution policy (window " f"-{args.pre}h/+{args.post}h):")
    print(header)
    print("  " + "-" * (len(header) - 2))
    for policy in ("primary", "all", "study"):
        events = to_study_events(frame, policy)
        report(events, args.pre, args.post, f"usd={policy}")

    print(f"\nWindow width (usd={args.usd_policy}):")
    print(header)
    print("  " + "-" * (len(header) - 2))
    events = to_study_events(frame, args.usd_policy)
    for pre, post in ((0, 1), (1, 2), (1, 4), (2, 6), (4, 12)):
        report(events, pre, post, f"-{pre}h / +{post}h")

    print("\nPer-pair breakdown for the chosen configuration:")
    plan = plan_fetch_hours(events, pre_hours=args.pre, post_hours=args.post)
    for pair, hours in sorted(plan.hours_by_pair.items()):
        print(f"  {pair:<10}{len(hours):>8,} hours")

    print("\nNotes:")
    print("  dedup = share of naive per-event hours removed by overlapping-window collapse")
    print(f"  fast/slow assume {SECONDS_PER_HOUR_LOW:.0f}-{SECONDS_PER_HOUR_HIGH:.0f}s per hour "
          "(measured against the live endpoint, includes throttling)")
    print("  the fetch is cached and resumable, so it can run in stages")


if __name__ == "__main__":
    main()

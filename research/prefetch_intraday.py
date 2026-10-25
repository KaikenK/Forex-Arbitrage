"""Prefetch event-windowed Dukascopy ticks for the causal sample, in parallel.

Sizing lives in `plan_intraday` (downloads nothing); this script does the download.
It is resumable: re-run the same command after an interruption and it continues.

Pilot first (RESEARCH_TRACKER §6, 2026-10-24) — measure throughput and throttling:
  python -m research.prefetch_intraday --pairs EUR/USD --limit 50

Full run, EUR/USD first because it is the positive control:
  python -m research.prefetch_intraday --pairs EUR/USD
  python -m research.prefetch_intraday
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from research.data.cache_paths import intraday_cache_dir
from research.data.event_windows import FetchPlan, plan_fetch_hours
from research.data.prefetch import DEFAULT_WORKERS, MAX_WORKERS, PrefetchReport, prefetch_plan
from research.data.tagging import USD_STUDY_PAIRS
from research.plan_intraday import CALENDAR_STORE, load_causal_sample, to_study_events

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
RUN_LOG = ARTIFACTS / "prefetch_runs.jsonl"
# Logged decision (2026-08): intraday horizons are capped at 120m, so −1h/+2h covers them.
DEFAULT_PRE_HOURS = 1
DEFAULT_POST_HOURS = 2
MIN_FREE_DISK_GB = 2.0


def _progress(report: PrefetchReport, completed: int, eta_seconds: float | None) -> None:
    eta = f"{eta_seconds / 60:5.1f} min" if eta_seconds is not None else "    ?    "
    print(
        f"  {completed:>6}/{report.attempted:<6} downloaded {report.downloaded:>6}  empty {report.empty:>5}  "
        f"failed {report.failed:>4}  429s {report.throttled:>4}  {report.hours_per_second:5.2f} h/s  ETA {eta}",
        flush=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Parallel, resumable intraday prefetch.")
    parser.add_argument("--store", type=Path, default=CALENDAR_STORE)
    parser.add_argument("--pairs", default=",".join(USD_STUDY_PAIRS),
                        help="Comma-separated subset of the study pairs (default: all four).")
    parser.add_argument("--pre", type=int, default=DEFAULT_PRE_HOURS)
    parser.add_argument("--post", type=int, default=DEFAULT_POST_HOURS)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help=f"Parallel downloads (capped at {MAX_WORKERS}).")
    parser.add_argument("--limit", type=int, default=None,
                        help="Attempt at most this many uncached hours (pilot run).")
    parser.add_argument("--cache-dir", type=Path, default=intraday_cache_dir())
    args = parser.parse_args()

    requested = [pair.strip().upper() for pair in args.pairs.split(",") if pair.strip()]
    unknown = [pair for pair in requested if pair not in USD_STUDY_PAIRS]
    if unknown:
        print(f"Not study pairs: {unknown}. Study pairs: {list(USD_STUDY_PAIRS)}", file=sys.stderr)
        return 2

    args.cache_dir.mkdir(parents=True, exist_ok=True)
    free_gb = shutil.disk_usage(args.cache_dir).free / 1e9
    if free_gb < MIN_FREE_DISK_GB:
        print(f"Only {free_gb:.1f} GB free at {args.cache_dir}; need {MIN_FREE_DISK_GB} GB.", file=sys.stderr)
        return 3

    frame = load_causal_sample(args.store)
    events = [event for event in to_study_events(frame, "study") if event.pair in requested]
    planned = plan_fetch_hours(events, pre_hours=args.pre, post_hours=args.post)
    # Keep the requested order, so EUR/USD (the positive control) is fetched first.
    plan = FetchPlan({pair: planned.hours_by_pair[pair] for pair in requested if pair in planned.hours_by_pair})

    print(f"Causal sample: {len(frame)} releases -> {len(events)} study observations in {requested}")
    print(f"Window -{args.pre}h/+{args.post}h -> {plan.summary()}")
    print(f"Cache: {args.cache_dir}  ({free_gb:.0f} GB free)   workers: {min(args.workers, MAX_WORKERS)}"
          + (f"   pilot limit: {args.limit}" if args.limit else ""))

    report = prefetch_plan(plan, args.cache_dir, workers=args.workers, max_hours=args.limit, progress=_progress)

    print(f"\nDone in {report.elapsed_seconds / 60:.1f} min: downloaded {report.downloaded}, "
          f"already cached {report.already_cached}, empty {report.empty}, failed {report.failed}, "
          f"429s {report.throttled}, {report.hours_per_second:.2f} h/s"
          + ("  [interrupted - re-run to resume]" if report.interrupted else ""))
    for failure in report.failures[:5]:
        print(f"  failed {failure['pair']} {failure['hour']}: {failure['error']}")

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with RUN_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "finished_at": datetime.now(UTC).isoformat(),
            "pairs": requested,
            "window": [args.pre, args.post],
            "workers": min(args.workers, MAX_WORKERS),
            "limit": args.limit,
            "plan_hours": plan.total_hours,
            **report.to_dict(),
        }) + "\n")
    return 1 if report.interrupted else 0


if __name__ == "__main__":
    raise SystemExit(main())

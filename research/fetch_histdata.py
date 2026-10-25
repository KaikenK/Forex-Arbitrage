"""Download HistData yearly 1-minute zips for the study pairs.

Replaces the hour-by-hour Dukascopy fetch for the majors (RESEARCH_TRACKER §6, 2026-10-24):
~8 files per pair instead of ~5,000 requests. Sequential with a polite pause between
downloads — there are only a few dozen files, so parallelism would buy nothing and would
be impolite to a free service. Resumable: cached years are skipped.

  python -m research.fetch_histdata
  python -m research.fetch_histdata --pairs EUR/USD --years 2018-2019
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from research.data.cache_paths import histdata_cache_dir
from research.data.histdata import HistDataError, download_year
from research.data.tagging import USD_STUDY_PAIRS

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
RUN_LOG = ARTIFACTS / "histdata_runs.jsonl"
DEFAULT_YEARS = "2018-2025"  # the calendar causal sample spans 2018-01 .. 2025-04
DEFAULT_PAUSE_SECONDS = 3.0


def _years(spec: str) -> list[int]:
    if "-" in spec:
        start, end = (int(part) for part in spec.split("-", 1))
        return list(range(start, end + 1))
    return [int(part) for part in spec.split(",")]


def main() -> int:
    parser = argparse.ArgumentParser(description="Download HistData yearly M1 zips.")
    parser.add_argument("--pairs", default=",".join(USD_STUDY_PAIRS))
    parser.add_argument("--years", default=DEFAULT_YEARS, help="e.g. 2018-2025 or 2018,2020")
    parser.add_argument("--pause", type=float, default=DEFAULT_PAUSE_SECONDS)
    parser.add_argument("--cache-dir", type=Path, default=histdata_cache_dir())
    args = parser.parse_args()

    pairs = [pair.strip().upper() for pair in args.pairs.split(",") if pair.strip()]
    years = _years(args.years)
    print(f"{len(pairs)} pairs x {len(years)} years -> {args.cache_dir}")

    started = time.monotonic()
    downloaded, cached, failed = 0, 0, []
    for pair in pairs:
        for year in years:
            try:
                path, fresh = download_year(pair, year, args.cache_dir)
            except (HistDataError, OSError) as exc:
                failed.append({"pair": pair, "year": year, "error": f"{type(exc).__name__}: {exc}"[:200]})
                print(f"  {pair} {year}: FAILED {exc}", flush=True)
                continue
            if fresh:
                downloaded += 1
                print(f"  {pair} {year}: {path.stat().st_size / 1e6:5.2f} MB", flush=True)
                time.sleep(args.pause)
            else:
                cached += 1
    elapsed = time.monotonic() - started

    print(f"\nDone in {elapsed:.0f} s: downloaded {downloaded}, already cached {cached}, failed {len(failed)}")
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with RUN_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "finished_at": datetime.now(UTC).isoformat(),
            "pairs": pairs,
            "years": years,
            "downloaded": downloaded,
            "already_cached": cached,
            "failed": failed,
            "elapsed_seconds": round(elapsed, 1),
        }) + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

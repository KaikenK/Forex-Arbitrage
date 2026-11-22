"""C45 (pre-registered 2026-11-21): the release clocks used to define clock-clear signal times.

Per currency, the local clock times (HH:MM) at which at least 2 % of that currency's high-impact,
exact-time releases occurred in 2019-10-01 .. 2025-04-07 (the existing calendar store, the same
release filter as window30). Written once, before any C45 data is extracted, to
`artifacts/c45_release_clocks.json`.

  python -m research.c45_release_clocks [--calendar <calendar_events.parquet>]
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from research.run_window30 import release_times_from_calendar

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
OUT = ARTIFACTS / "c45_release_clocks.json"
DEFAULT_CALENDAR = ARTIFACTS / "calendar_events.parquet"
ZONES = {"USD": "America/New_York", "EUR": "Europe/Berlin", "GBP": "Europe/London",
         "JPY": "Asia/Tokyo", "CHF": "Europe/Zurich"}
START, END = pd.Timestamp("2019-10-01", tz="UTC"), pd.Timestamp("2025-04-08", tz="UTC")
MIN_SHARE = 0.02


def release_clocks(release_times: dict[str, list[float]], *, min_share: float = MIN_SHARE) -> dict:
    out = {}
    for currency, zone in ZONES.items():
        stamps = [pd.Timestamp(t, unit="s", tz="UTC") for t in release_times.get(currency, ())]
        stamps = [s for s in stamps if START <= s < END]
        clocks = Counter(s.tz_convert(zone).strftime("%H:%M") for s in stamps)
        n = sum(clocks.values())
        kept = {c: k for c, k in sorted(clocks.items()) if n and k / n >= min_share}
        out[currency] = {"zone": zone, "releases": n, "clocks": sorted(kept),
                         "share_covered": round(sum(kept.values()) / n, 4) if n else None,
                         "counts": kept}
    return out


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--calendar", type=Path, default=DEFAULT_CALENDAR)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    if args.out.exists():
        raise SystemExit(f"{args.out} exists; the C45 clock set is written once and never overwritten.")
    clocks = release_clocks(release_times_from_calendar(pd.read_parquet(args.calendar)))
    report = {"rule": "C45 (RESEARCH_TRACKER §6, 2026-11-21): local clocks holding >= 2 % of a currency's "
                      "high-impact exact-time releases, 2019-10-01..2025-04-07",
              "calendar": str(args.calendar), "min_share": MIN_SHARE,
              "generated_at": datetime.now(UTC).isoformat(), "currencies": clocks}
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

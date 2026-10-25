"""Export reaction training rows from US calendar releases + HistData 1-minute bars.

Reads the research pipeline's outputs (it does not recompute them):
  - research/artifacts/calendar_events.parquet  (Option A standardized surprises, exact times)
  - the HistData M1 cache (%LOCALAPPDATA%\\arbex\\histdata, research/data/cache_paths.py)
  - research/eventstudy/sign_conventions.py      (whether a higher print is good news)

so `research/` must be importable: pass --research-root (the directory that *contains*
research/), or set ARBEX_RESEARCH_ROOT. Row construction lives in
`training/calendar_reaction_rows.py`; see its docstring for the labelling rules.

Run (from services/semantic):
  python scripts/export_calendar_reaction_rows.py --research-root <checkout> \
      --output data/calendar_reaction_rows.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = SERVICE_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from market.pair_assets import ticker_for  # noqa: E402
from training.calendar_reaction_rows import (  # noqa: E402
    DEFAULT_HORIZONS,
    STUDY_PAIRS,
    VIEWS,
    CalendarRelease,
    MinuteBars,
    build_release_rows,
    hours_needed,
    release_id_for,
)

FIRST_YEAR, LAST_YEAR = 2018, 2025  # HistData cache coverage


def main() -> int:
    args = _parse_args()
    research_root = _research_root(args.research_root)
    sys.path.insert(0, str(research_root))
    import pandas as pd
    from research.data.cache_paths import histdata_cache_dir
    from research.data.histdata import load_windowed_series
    from research.eventstudy.sign_conventions import news_sign

    store = args.calendar_store or research_root / "research" / "artifacts" / "calendar_events.parquet"
    frame = pd.read_parquet(store)
    releases, skipped = _us_releases(frame, news_sign)
    print(f"{len(releases)} US high-impact releases with exact time + standardized surprise "
          f"({skipped['ambiguous_sign']} skipped: ambiguous sign; {skipped['out_of_range']} outside "
          f"{FIRST_YEAR}-{LAST_YEAR})")

    horizons = [int(h) for h in args.horizons.split(",")]
    hours = hours_needed(releases, horizons)
    cache_dir = args.histdata_dir or histdata_cache_dir()
    bars_by_pair: dict[str, MinuteBars] = {}
    for pair in STUDY_PAIRS:
        series, missing = load_windowed_series(pair, hours, cache_dir)
        if missing:
            print(f"  {pair}: MISSING HistData years {missing}")
        bars_by_pair[pair] = MinuteBars(series.times, series.prices)
        print(f"  {pair}: {len(series.times):,} bars over {len(hours):,} planned hours")

    rows: list[dict[str, object]] = []
    for release in releases:
        rows.extend(build_release_rows(release, bars_by_pair, horizons=horizons, views=VIEWS,
                                       ticker_for=ticker_for))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp_path = args.output.with_suffix(args.output.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    os.replace(temp_path, args.output)

    summary = _summary(rows, releases, store, horizons)
    summary_path = args.output.with_name(args.output.stem + "_summary.json")
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n{len(rows):,} rows -> {args.output}")
    print(json.dumps(summary["rows_by_pair_horizon"], indent=2))
    print(f"release moments with >= 1 row: {summary['release_moments_with_rows']}")
    return 0


def _us_releases(frame, news_sign) -> tuple[list[CalendarRelease], Counter]:
    import pandas as pd

    sample = frame[
        (frame["country"] == "United States")
        & (frame["importance"] == "high")
        & (frame["timestamp_precision_minutes"] == 0)
        & frame["standardized_surprise"].notna()
    ]
    skipped: Counter = Counter()
    releases: list[CalendarRelease] = []
    for row in sample.to_dict("records"):
        timestamp = pd.Timestamp(row["timestamp"]).to_pydatetime()
        timestamp = timestamp.replace(tzinfo=UTC) if timestamp.tzinfo is None else timestamp.astimezone(UTC)
        if not FIRST_YEAR <= timestamp.year <= LAST_YEAR:
            skipped["out_of_range"] += 1
            continue
        sign = news_sign("United States", str(row["event_name"]))
        if sign is None:
            skipped["ambiguous_sign"] += 1
            continue
        releases.append(CalendarRelease(
            # One release *moment*: payrolls, unemployment and earnings print together and
            # share one price path, so they must share a split group.
            release_id=release_id_for("moment", timestamp),
            timestamp=timestamp,
            event_name=str(row["event_name"]),
            country="United States",
            actual=_optional_float(row.get("actual")),
            forecast=_optional_float(row.get("forecast")),
            previous=_optional_float(row.get("previous")),
            standardized_surprise=float(row["standardized_surprise"]),
            news_sign=int(sign),
            timestamp_source=str(row.get("timestamp_source") or "source"),
            in_pandemic_window=bool(row.get("in_pandemic_window") or False),
        ))
    releases.sort(key=lambda release: (release.timestamp, release.event_name))
    return releases, skipped


def _summary(rows, releases, store: Path, horizons: list[int]) -> dict[str, object]:
    by_pair_horizon: dict[str, dict[str, int]] = {}
    for row in rows:
        if row["source_metadata"]["row_view"] != "live":
            continue  # each (release, pair, horizon) appears once per view; count it once
        cell = by_pair_horizon.setdefault(str(row["pair"]), {})
        cell[str(row["horizon_minutes"])] = cell.get(str(row["horizon_minutes"]), 0) + 1
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "calendar_store": str(store),
        "horizons": horizons,
        "views": list(VIEWS),
        "label_orientation": "usd",
        "releases": len(releases),
        "release_moments": len({r.release_id for r in releases}),
        "release_moments_with_rows": len({row["source_metadata"]["release_id"] for row in rows}),
        "rows": len(rows),
        "rows_by_pair_horizon": by_pair_horizon,
        "indicators": dict(Counter(r.event_name for r in releases).most_common()),
    }


def _optional_float(value) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number  # NaN -> None


def _research_root(explicit: Path | None) -> Path:
    candidates = [explicit, Path(os.environ["ARBEX_RESEARCH_ROOT"]) if os.environ.get("ARBEX_RESEARCH_ROOT") else None,
                  SERVICE_ROOT.parents[1]]
    for candidate in candidates:
        if candidate is not None and (candidate / "research" / "data" / "histdata.py").exists():
            return candidate.resolve()
    raise SystemExit("research/ not found: pass --research-root <dir containing research/> or set ARBEX_RESEARCH_ROOT.")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--research-root", type=Path, default=None)
    parser.add_argument("--calendar-store", type=Path, default=None)
    parser.add_argument("--histdata-dir", type=Path, default=None)
    parser.add_argument("--horizons", default=",".join(str(h) for h in DEFAULT_HORIZONS))
    parser.add_argument("--output", type=Path, default=SERVICE_ROOT / "data" / "calendar_reaction_rows.jsonl")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())

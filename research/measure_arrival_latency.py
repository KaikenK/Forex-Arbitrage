"""Release-to-arrival latency of live news (RESEARCH_TRACKER §6, pre-registered 2026-11-02).

Rows: `live_predictions.jsonl` with basis = release and signal_scope = article, logged in
[--since, --until); one row per (release_event, release_utc, feed_url), earliest receipt kept.
  receipt latency    our receipt time (`published_at`) − release_utc, minutes
  publisher latency  `publisher_time` − release_utc, minutes
  effective latency  per release, the first receipt across all sources
Observed offset a* = median effective latency rounded UP to {1, 2, 3, 5, 10, 15}; not estimated
with fewer than MIN_RELEASES distinct releases. Coverage uses the weekly calendar archive.

Run: python -m research.measure_arrival_latency --predictions <jsonl> --calendar-archive <dir>
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
OFFSETS = (1, 2, 3, 5, 10, 15)
MIN_RELEASES = 10
STUDY_CURRENCIES = {"USD", "EUR", "GBP", "JPY", "CHF", "INR"}
SINCE = datetime(2026, 11, 1, 21, 49, tzinfo=UTC)          # 2026-11-02 03:19 IST
UNTIL = datetime(2026, 11, 13, 18, 29, tzinfo=UTC)         # 2026-11-13 23:59 IST


def observed_offset(minutes: list[float]) -> int | None:
    if len(minutes) < MIN_RELEASES:
        return None
    median = float(np.median(minutes))
    return next((o for o in OFFSETS if median <= o), OFFSETS[-1])


def _summary(values: pd.Series) -> dict:
    v = values.dropna().to_numpy(dtype=float)
    if not len(v):
        return {"n": 0}
    return {"n": int(len(v)), "median_min": float(np.median(v)), "p25_min": float(np.quantile(v, 0.25)),
            "p75_min": float(np.quantile(v, 0.75)),
            **{f"within_{m}_min": float(np.mean(v <= m)) for m in (1, 2, 5, 15)}}


def load(path: Path, since: datetime, until: datetime) -> pd.DataFrame:
    rows = [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]
    frame = pd.DataFrame(rows)
    if "basis" not in frame:
        return frame.iloc[0:0]
    frame = frame[(frame["basis"] == "release") & (frame["signal_scope"] == "article")].copy()
    for col in ("published_at", "release_utc", "publisher_time", "logged_at"):
        frame[col] = pd.to_datetime(frame.get(col), utc=True, errors="coerce", format="ISO8601")
    frame = frame[(frame["logged_at"] >= since) & (frame["logged_at"] < until) & frame["release_utc"].notna()]
    frame["feed_url"] = frame["feed_url"].fillna("unknown")
    frame = frame.sort_values("published_at").drop_duplicates(["release_event", "release_utc", "feed_url"])
    frame["receipt_latency_min"] = (frame["published_at"] - frame["release_utc"]).dt.total_seconds() / 60
    frame["publisher_latency_min"] = (frame["publisher_time"] - frame["release_utc"]).dt.total_seconds() / 60
    return frame


def calendar_coverage(archive: Path | None, matched: set[tuple[str, str]], since: datetime, until: datetime) -> dict | None:
    if archive is None or not archive.exists():
        return None
    events = set()
    for file in sorted(archive.glob("ff_calendar_*.json")):
        for row in json.loads(file.read_text(encoding="utf-8")):
            if str(row.get("impact", "")).lower() != "high" or str(row.get("country", "")).upper() not in STUDY_CURRENCIES:
                continue
            try:
                stamp = datetime.fromisoformat(str(row["date"]).replace("Z", "+00:00")).astimezone(UTC)
            except (KeyError, ValueError):
                continue
            if since <= stamp < until and (stamp.hour, stamp.minute) != (0, 0):
                events.add((str(row.get("title", "")), stamp.isoformat()))
    if not events:
        return {"calendar_events": 0}
    hit = sum(1 for e in events if e in matched)
    return {"calendar_events": len(events), "matched": hit, "coverage": hit / len(events)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--calendar-archive", type=Path, default=None)
    parser.add_argument("--since", type=lambda s: datetime.fromisoformat(s).astimezone(UTC), default=SINCE)
    parser.add_argument("--until", type=lambda s: datetime.fromisoformat(s).astimezone(UTC), default=UNTIL)
    args = parser.parse_args()
    frame = load(args.predictions, args.since, args.until)
    first = frame.groupby(["release_event", "release_utc"])["receipt_latency_min"].min() if len(frame) else pd.Series(dtype=float)
    matched = {(e, t.isoformat()) for e, t in first.index} if len(first) else set()
    a_star = observed_offset([m for m in first.tolist() if math.isfinite(m)])
    result = {
        "pre_registration": "RESEARCH_TRACKER §6 ARRIVAL-LATENCY STUDY (2026-11-02)",
        "since": args.since.isoformat(), "until": args.until.isoformat(), "computed_at": datetime.now(UTC).isoformat(),
        "rows": int(len(frame)), "releases": int(len(first)),
        "receipt_latency": _summary(frame["receipt_latency_min"]) if len(frame) else {"n": 0},
        "publisher_latency": _summary(frame["publisher_latency_min"]) if len(frame) else {"n": 0},
        "effective_latency": _summary(first),
        "by_source": {src: _summary(g["receipt_latency_min"]) for src, g in frame.groupby("feed_url")} if len(frame) else {},
        "observed_offset_min": a_star,
        "offset_rule": f"median effective latency rounded up to {OFFSETS}; None if < {MIN_RELEASES} releases",
        "coverage": calendar_coverage(args.calendar_archive, matched, args.since, args.until),
    }
    out = ARTIFACTS / "arrival_latency.json"
    out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("rows", "releases", "effective_latency", "observed_offset_min")}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

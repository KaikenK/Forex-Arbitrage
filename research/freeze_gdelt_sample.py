"""Freeze the H1/H5/H6 GDELT sample at the pre-registered cutoff (RESEARCH_TRACKER §6, LOCKED
PROTOCOL 2026-10-25; cutoff moved by DEVIATION 1 and 2; sample re-collected by DEVIATION 3,
2026-11-02, before any H1/H5/H6 result existed).

Locked rule, as amended by DEVIATION 3: H1/H5/H6 use only the scheduled time-stratified slices —
1 h windows ending 07, 09, 11, 13, 14, 15, 17, 20 UTC on weekdays, 2023-07-01..2025-04-30
(`gdelt_backfill.scheduled_window`, shard dir `artifacts/gdelt_slices_main`) — whose shard
completed before the cutoff, 2026-11-10 23:59 IST (18:29 UTC). The shard list is frozen at the
cutoff with a content hash. Coverage (slices done / scheduled, events) is reported as is: the
cutoff is not moved and gaps are not selectively filled. A day with any scheduled slice missing
is listed in `missing_windows` (the unit C17 drops). Data downloaded after the cutoff is not used.

Run at or after the cutoff (it refuses earlier, and refuses to overwrite an existing freeze):
  python -m research.freeze_gdelt_sample
  python -m research.freeze_gdelt_sample --preview      # print what would qualify now, write nothing

Completion time of a window = `completed_at` in its `.done` sentinel. Sentinels written before
that field existed (backfill runs up to 2026-10-25) fall back to the sentinel file's mtime;
the freeze records how many windows used each source, and how far mtime and the older
`fetched_at` field disagree, so the fallback is auditable.

The hash is SHA-256 over, for each qualifying window in sorted order, its name followed by the
bytes of its shard parquet (empty for a confirmed-empty window). `verify_freeze` recomputes it;
`run_h1`, `run_h5` and `score_archive` read events only through `load_frozen_events`, which
verifies first.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd

from research.gdelt_backfill import _window_name, scheduled_window

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
SHARD_DIR = ARTIFACTS / "gdelt_slices_main"
FREEZE_PATH = ARTIFACTS / "gdelt_sample_freeze.json"

CUTOFF_UTC = datetime(2026, 11, 10, 18, 29, tzinfo=UTC)      # = 2026-11-10 23:59 IST (DEVIATION 3)
SAMPLE_START = date(2023, 7, 1)
SAMPLE_END = date(2025, 4, 30)                                 # inclusive
SLICE_HOURS = (7, 9, 11, 13, 14, 15, 17, 20)                   # UTC window-end hours, 1 h windows
WEEKDAYS_ONLY = True


def expected_windows(start: date = SAMPLE_START, end: date = SAMPLE_END) -> list[str]:
    """Names of every scheduled slice in [start, end], exactly as the backfill names them."""
    names = []
    hours = set(SLICE_HOURS)
    cursor = datetime.combine(start, datetime.min.time(), tzinfo=UTC)
    stop = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    while cursor < stop:
        window_end = cursor + timedelta(hours=1)
        if scheduled_window(cursor, window_end, hours, WEEKDAYS_ONLY):
            names.append(_window_name(cursor))
        cursor = window_end
    return names


EXPECTED = frozenset(expected_windows())
EXPECTED_WINDOWS = len(EXPECTED)


class FreezeError(RuntimeError):
    """The freeze is missing, premature, already written, or no longer matches the shards."""


def sentinel_completed_at(sentinel: Path) -> tuple[datetime, str, dict]:
    """(completion time UTC, "completed_at" | "mtime", parsed sentinel)."""
    try:
        meta = json.loads(sentinel.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        meta = {}
    value = meta.get("completed_at")
    if value:
        stamp = datetime.fromisoformat(value)
        return (stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)).astimezone(UTC), "completed_at", meta
    return datetime.fromtimestamp(sentinel.stat().st_mtime, tz=UTC), "mtime", meta


def _shard_bytes(shard_dir: Path, name: str) -> bytes:
    path = shard_dir / f"{name}.parquet"
    return path.read_bytes() if path.exists() else b""


def content_hash(shard_dir: Path, names: list[str]) -> str:
    digest = hashlib.sha256()
    for name in sorted(names):
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(_shard_bytes(shard_dir, name))
        digest.update(b"\0")
    return digest.hexdigest()


def build_freeze(shard_dir: Path = SHARD_DIR, cutoff: datetime = CUTOFF_UTC) -> dict:
    """Which windows qualify at `cutoff`, with counts, provenance and the content hash."""
    qualifying: list[dict] = []
    excluded_after_cutoff: list[str] = []
    sources = {"completed_at": 0, "mtime": 0}
    mtime_vs_fetched: list[float] = []
    for sentinel in sorted(Path(shard_dir).glob("*.done")):
        name = sentinel.stem
        if name not in EXPECTED:                      # off-schedule or outside the period
            continue
        completed, source, meta = sentinel_completed_at(sentinel)
        if completed >= cutoff:
            excluded_after_cutoff.append(name)
            continue
        sources[source] += 1
        if source == "mtime" and meta.get("fetched_at"):
            fetched = datetime.fromisoformat(meta["fetched_at"])
            fetched = fetched if fetched.tzinfo else fetched.replace(tzinfo=UTC)
            mtime_vs_fetched.append(abs((completed - fetched).total_seconds()))
        shard = Path(shard_dir) / f"{name}.parquet"
        events = len(pd.read_parquet(shard, columns=["event_id"])) if shard.exists() else 0
        qualifying.append({
            "window": name, "events": events, "completed_at": completed.isoformat(),
            "completed_at_source": source,
            # 0 events without confirmed_empty: written by a pre-2026-10-24 run that recorded
            # GDELT rate-limit notices as empty days. Kept (the cutoff is not cherry-picked) but
            # counted, so coverage can be read both ways.
            "unverified_empty": events == 0 and not meta.get("confirmed_empty", False),
        })
    names = [row["window"] for row in qualifying]
    unverified = sum(row["unverified_empty"] for row in qualifying)
    return {
        "cutoff_utc": cutoff.isoformat(),
        "sample": [SAMPLE_START.isoformat(), SAMPLE_END.isoformat()],
        "schedule": {"slice_end_hours_utc": list(SLICE_HOURS), "window_hours": 1, "weekdays_only": WEEKDAYS_ONLY},
        "expected_windows": EXPECTED_WINDOWS,
        "windows_done": len(names),
        "coverage": round(len(names) / EXPECTED_WINDOWS, 4),
        "coverage_excluding_unverified_empty": round((len(names) - unverified) / EXPECTED_WINDOWS, 4),
        "unverified_empty_windows": unverified,
        "events": int(sum(row["events"] for row in qualifying)),
        "completion_time_source_counts": sources,
        "mtime_vs_fetched_at_seconds": (
            {"n": len(mtime_vs_fetched), "max": max(mtime_vs_fetched), "median": sorted(mtime_vs_fetched)[len(mtime_vs_fetched) // 2]}
            if mtime_vs_fetched else None),
        "excluded_completed_after_cutoff": excluded_after_cutoff,
        "missing_windows": _missing_days(names),
        "missing_slices": sorted(EXPECTED - set(names)),
        "sha256": content_hash(Path(shard_dir), names),
        "shards": qualifying,
    }


def _missing_days(names: list[str]) -> list[str]:
    """ISO dates with at least one scheduled slice missing (C17: such a day is dropped)."""
    missing = EXPECTED - set(names)
    return sorted({f"{n[:4]}-{n[4:6]}-{n[6:8]}" for n in missing})


def write_freeze(
    shard_dir: Path = SHARD_DIR,
    out: Path = FREEZE_PATH,
    *,
    cutoff: datetime = CUTOFF_UTC,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(UTC)
    if now < cutoff:
        raise FreezeError(f"it is {now.isoformat()}; the freeze can only be written at/after {cutoff.isoformat()}")
    if out.exists():
        raise FreezeError(f"{out} already exists; the freeze is written once and never moved")
    freeze = {**build_freeze(shard_dir, cutoff), "frozen_at_utc": now.isoformat()}
    temp = out.with_name(f"{out.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(freeze, indent=2), encoding="utf-8")
    os.replace(temp, out)
    return freeze


def load_freeze(path: Path = FREEZE_PATH) -> dict:
    if not path.exists():
        raise FreezeError(f"no frozen sample at {path}; run `python -m research.freeze_gdelt_sample` "
                          f"at/after {CUTOFF_UTC.isoformat()} before H1/H5")
    return json.loads(path.read_text(encoding="utf-8"))


def verify_freeze(freeze: dict, shard_dir: Path = SHARD_DIR) -> None:
    names = [row["window"] for row in freeze["shards"]]
    actual = content_hash(Path(shard_dir), names)
    if actual != freeze["sha256"]:
        raise FreezeError("frozen shards changed since the freeze (hash mismatch); refusing to analyse")


def load_frozen_events(freeze: dict, shard_dir: Path = SHARD_DIR) -> pd.DataFrame:
    """The frozen sample as an event store: listed shards only, deduplicated by event_id and
    re-tagged with the current tagger, exactly as `gdelt_backfill.consolidate` does."""
    from research.gdelt_backfill import retag

    verify_freeze(freeze, shard_dir)
    frames = [pd.read_parquet(Path(shard_dir) / f"{row['window']}.parquet")
              for row in freeze["shards"] if row["events"] > 0]
    if not frames:
        return pd.DataFrame()
    return retag(pd.concat(frames, ignore_index=True).drop_duplicates("event_id"))


def frozen_events_or_none(path: Path = FREEZE_PATH, shard_dir: Path = SHARD_DIR) -> tuple[pd.DataFrame, dict] | None:
    """(events, freeze) when a freeze exists, else None. Raises on a hash mismatch."""
    if not path.exists():
        return None
    freeze = load_freeze(path)
    return load_frozen_events(freeze, shard_dir), freeze


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze the H1/H5 GDELT sample at the locked cutoff.")
    parser.add_argument("--preview", action="store_true", help="report what qualifies now; write nothing")
    parser.add_argument("--shard-dir", type=Path, default=SHARD_DIR)
    parser.add_argument("--out", type=Path, default=FREEZE_PATH)
    args = parser.parse_args()
    if args.preview:
        summary = build_freeze(args.shard_dir)
        summary.pop("shards")
        summary["missing_windows"] = len(summary["missing_windows"])
        summary["missing_slices"] = len(summary["missing_slices"])
        print(json.dumps(summary, indent=2))
        return
    freeze = write_freeze(args.shard_dir, args.out)
    print(f"Frozen {freeze['windows_done']}/{EXPECTED_WINDOWS} slices ({freeze['coverage']:.1%}), "
          f"{freeze['events']} events, sha256 {freeze['sha256']} -> {args.out}")
    print(f"Excluded (completed after cutoff): {len(freeze['excluded_completed_after_cutoff'])}; "
          f"completion source: {freeze['completion_time_source_counts']}")


if __name__ == "__main__":
    main()

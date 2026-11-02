"""DEVIATION 4: build the news sample from the GDELT GKG extraction on BigQuery.

The extraction (`arbex-gdelt.gdelt_fx.fx_headlines`, logged in RESEARCH_TRACKER §6) holds every
GKG article 2018-01..2025-04 whose page title matched the recall filter. This module turns each
month of it into one shard in the existing format (`gdelt_backfill._write_window`: parquet of
`RawEvent` rows + `.done` sentinel with `completed_at`), so the freeze, `score_archive` and the
H1/H5/H6 runners read it exactly as they read DOC shards.

Per row, fixed by DEVIATION 4: title HTML-unescaped; event_id = first 16 hex of sha256(url), as
DOC ingestion; a URL that appears more than once keeps its earliest timestamp; currencies and
categories from the unchanged `data/tagging`; rows the tagger cannot attribute are dropped, as
DOC ingestion drops them. Timestamps are GDELT's 15-minute batch time (UTC).

GKG carries page titles only from September 2019, so the 2018-01..2019-08 shards are empty and
2019-09 is partial; the H1/H5/H6 sample starts 2019-10 (DEVIATION 5,
`freeze_gdelt_sample.SAMPLE_START`). Ingestion still covers the whole extraction.

Runs where BigQuery credentials exist (Google Colab, see research/colab/). The table is read once
through tabledata.list (no query quota) and split by month. Resumable: a month with a sentinel is
skipped.

  python -m research.gkg_ingest --project arbex-gdelt
  python -m research.gkg_ingest --project arbex-gdelt --daily-counts-only
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
from collections.abc import Iterable
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd

from research.data.base import RawEvent
from research.data.tagging import tag_categories, tag_currencies

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
SHARD_DIR = ARTIFACTS / "gdelt_gkg_shards"
DAILY_COUNTS = ARTIFACTS / "gdelt_gkg_daily_counts.json"
TABLE = "gdelt_fx.fx_headlines"
COUNTS_TABLE = "gdelt_fx.daily_counts"
FIRST_MONTH = date(2018, 1, 1)
LAST_MONTH = date(2025, 4, 1)
SOURCE = "gdelt_gkg"


def months(first: date = FIRST_MONTH, last: date = LAST_MONTH) -> list[date]:
    out, current = [], first
    while current <= last:
        out.append(current)
        current = date(current.year + (current.month == 12), current.month % 12 + 1, 1)
    return out


def shard_name(month: date) -> str:
    """Same naming as DOC windows: the window start, `YYYYMMDDTHHMMSS`."""
    return month.strftime("%Y%m%dT000000")


def event_id_for(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def rows_to_events(frame: pd.DataFrame) -> list[RawEvent]:
    """BigQuery rows (ts, url, domain, title) -> tagged RawEvents (see module docstring)."""
    if frame.empty:
        return []
    data = frame.copy()
    data["ts"] = pd.to_datetime(data["ts"], utc=True)
    data = data.dropna(subset=["ts", "url", "title"])
    data["title"] = data["title"].astype(str).map(lambda t: html.unescape(t).strip())
    data = data[data["title"] != ""]
    data = data.sort_values(["ts", "url"]).drop_duplicates("url", keep="first")
    events: list[RawEvent] = []
    for row in data.itertuples(index=False):
        currencies = tag_currencies(row.title)
        if not currencies:
            continue
        events.append(RawEvent(
            event_id=event_id_for(str(row.url)), timestamp=row.ts.to_pydatetime(), headline=row.title,
            source=SOURCE, url=str(row.url), domain=str(row.domain or ""), language="English",
            currencies=tuple(currencies), categories=tuple(tag_categories(row.title)), scheduled=False,
            timestamp_precision_minutes=15.0,
        ))
    return events


def fetch_table(client, project: str, table: str = TABLE) -> pd.DataFrame:
    """The whole extraction table through tabledata.list: reading a table this way scans nothing,
    so it costs no query quota (the sandbox has ~98 GB left in November 2026). ~1 GB, 3.7 M rows."""
    rows = client.list_rows(f"{project}.{table}")
    return rows.to_dataframe(create_bqstorage_client=False, progress_bar_type=None)


def fetch_month(client, project: str, month: date, *, _cache: dict = {}) -> pd.DataFrame:
    """One month of the table, from a single cached full read (see `fetch_table`)."""
    key = (id(client), project)
    if key not in _cache:
        frame = fetch_table(client, project)
        frame["month"] = pd.to_datetime(frame["month"]).dt.date
        _cache.clear()
        _cache[key] = {m: g[["ts", "url", "domain", "title"]] for m, g in frame.groupby("month")}
    return _cache[key].get(month, pd.DataFrame(columns=["ts", "url", "domain", "title"]))


def ingest(client, project: str, shard_dir: Path = SHARD_DIR, wanted: Iterable[date] | None = None) -> dict:
    from research.gdelt_backfill import _write_window

    shard_dir = Path(shard_dir)
    shard_dir.mkdir(parents=True, exist_ok=True)
    report = {"months": 0, "skipped_done": 0, "rows_fetched": 0, "events_written": 0}
    for month in (list(wanted) if wanted is not None else months()):
        report["months"] += 1
        name = shard_name(month)
        if (shard_dir / f"{name}.done").exists():
            report["skipped_done"] += 1
            continue
        frame = fetch_month(client, project, month)
        events = rows_to_events(frame)
        written = _write_window(shard_dir, name, events)
        report["rows_fetched"] += len(frame)
        report["events_written"] += written
        print(f"  {month:%Y-%m}: {len(frame):>7,} rows -> {written:>7,} tagged events", flush=True)
    return report


def fetch_daily_counts(client, project: str, out: Path = DAILY_COUNTS) -> int:
    rows = client.list_rows(f"{project}.{COUNTS_TABLE}")                   # no query quota
    counts = {row["day"].isoformat(): int(row["n"]) for row in rows}
    out.write_text(json.dumps({"source": f"{project}.{COUNTS_TABLE}", "fetched_at": datetime.now(UTC).isoformat(),
                               "counts": dict(sorted(counts.items()))}, indent=1), encoding="utf-8")
    return len(counts)


def gap_days(counts: dict[str, int], first: date, last: date, *, window: int = 28, floor: float = 0.10) -> list[str]:
    """DEVIATION 4 rule: a UTC day whose unfiltered GKG count is zero or below `floor` x the
    median of the previous `window` days (those present) is a data gap."""
    series = pd.Series(counts, dtype=float)
    series.index = pd.to_datetime(series.index)
    full = series.reindex(pd.date_range(min(series.index.min(), pd.Timestamp(first)), last, freq="D"), fill_value=0.0)
    trailing = full.shift(1).rolling(window, min_periods=7).median()
    gaps = (full <= 0) | (full < floor * trailing)
    return [d.date().isoformat() for d, flag in gaps.items() if flag and first <= d.date() <= last]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", required=True)
    parser.add_argument("--shard-dir", type=Path, default=SHARD_DIR)
    parser.add_argument("--daily-counts-only", action="store_true")
    args = parser.parse_args()
    from google.cloud import bigquery

    client = bigquery.Client(project=args.project)
    days = fetch_daily_counts(client, args.project)
    print(f"daily counts: {days} days -> {DAILY_COUNTS}")
    if args.daily_counts_only:
        return 0
    report = ingest(client, args.project, args.shard_dir)
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

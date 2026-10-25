"""Rebuild the GDELT-arm feature table reproducibly, from one command.

  python -m research.build_features            # consolidate shards, then rebuild features
  python -m research.build_features --skip-consolidate

Why this exists (RESEARCH_TRACKER §8, 2026-10-24): the tracker recorded 4,473 feature rows
while `artifacts/features.parquet` held 2,009, and nothing recorded which store, tagger or
pair set produced either number. This script writes `features.manifest.json` next to the
table: the archive hash, shard count, event count, attribution policy and per-pair row
counts, so the next number can be traced.

Pair set: the 2026-10-24 study pairs plus AUD/USD. AUD/USD rows are *retained* but flagged
`study_pair = False` and excluded from analysis (§6). GDELT rows use usd_policy="none": a
USD-only headline has no single pair (same as `run_pipeline`).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from research.data import load_price_series
from research.data.tagging import DAILY_STUDY_PAIRS, INTRADAY_STUDY_PAIRS
from research.features import build_feature_table, build_market_context_fn, store_df_to_records
from research.gdelt_backfill import DEFAULT_SHARD_DIR, _atomic_write_bytes, consolidate
from research.run_pipeline import COVARIATES, HORIZONS, PAIR_TICKER

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
STORE = ARTIFACTS / "events.parquet"
OUT = ARTIFACTS / "features.parquet"
STUDY_PAIRS = INTRADAY_STUDY_PAIRS | DAILY_STUDY_PAIRS


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild the GDELT feature table.")
    parser.add_argument("--skip-consolidate", action="store_true")
    parser.add_argument("--store", type=Path, default=STORE)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()

    if not args.skip_consolidate:
        consolidate(DEFAULT_SHARD_DIR, args.store)
    frame = pd.read_parquet(args.store)
    records = [r for r in store_df_to_records(frame) if r["pair"] in PAIR_TICKER]
    pairs = sorted({r["pair"] for r in records})
    print(f"{len(frame)} events -> {len(records)} (event, pair) records across {pairs}")
    start = min(r["timestamp"] for r in records)
    end = max(r["timestamp"] for r in records)

    series_by_pair = {pair: load_price_series(PAIR_TICKER[pair], pair, start, end) for pair in pairs}
    covariates = {name: load_price_series(ticker, name, start, end) for name, ticker in COVARIATES.items()}
    table = build_feature_table(records, series_by_pair, horizons_minutes=HORIZONS,
                                context_fn=build_market_context_fn(covariates, series_by_pair),
                                max_entry_lag_minutes=4 * 1440)
    table["study_pair"] = table["pair"].isin(STUDY_PAIRS)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_bytes(args.out, lambda p: table.to_parquet(p, index=False))

    manifest = {
        "built_at": datetime.now(UTC).isoformat(),
        "command": " ".join(["python -m research.build_features", *sys.argv[1:]]),
        "store": str(args.store),
        "store_events": len(frame),
        "store_hash": hashlib.sha256("\n".join(sorted(frame["event_id"].astype(str))).encode()).hexdigest()[:16],
        "shards": len(list(DEFAULT_SHARD_DIR.glob("*.parquet"))),
        "store_range": [str(frame["timestamp"].min()), str(frame["timestamp"].max())],
        "usd_policy": "none",
        "horizons_minutes": HORIZONS,
        "rows": len(table),
        "rows_by_pair": table["pair"].value_counts().to_dict(),
        "study_rows": int(table["study_pair"].sum()),
    }
    manifest_path = args.out.with_suffix(".manifest.json")
    _atomic_write_bytes(manifest_path, lambda p: p.write_text(json.dumps(manifest, indent=2), encoding="utf-8"))
    print(f"{len(table)} rows ({manifest['study_rows']} in study pairs) -> {args.out}")
    print(f"manifest -> {manifest_path}")


if __name__ == "__main__":
    main()

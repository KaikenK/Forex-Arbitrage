"""End-to-end pipeline: event DB -> per-pair records -> canonical feature table -> impact atlas.

Runs the whole research stack on the real GDELT event store with real prices, proving every
stage composes. Daily price resolution for now (free, no key); swapping in intraday series
changes nothing downstream.

Run: python -m research.run_pipeline [--limit N] [--pairs EUR/USD,USD/INR]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from research.data import load_price_series
from research.eventstudy import align_events, build_atlas, correct_atlas
from research.eventstudy.schema import Event
from research.features import build_feature_table, build_market_context_fn, store_df_to_records

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
STORE = ARTIFACTS / "events.parquet"
OUT = ARTIFACTS / "features.parquet"

PAIR_TICKER = {
    "EUR/USD": "EURUSD=X", "GBP/USD": "GBPUSD=X", "USD/JPY": "USDJPY=X",
    "AUD/USD": "AUDUSD=X", "USD/CHF": "USDCHF=X", "USD/INR": "USDINR=X",
}
COVARIATES = {"DXY": "DX-Y.NYB", "VIX": "^VIX", "OIL": "CL=F", "US10Y": "^TNX"}
HORIZONS = [1440, 2880, 7200]  # 1d, 2d, 5d at daily resolution
MIN_CELL_N = 20                # minimum observations for a (category, horizon) cell to be tested


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="cap events (0 = all)")
    parser.add_argument("--pairs", default="", help="comma-separated subset of pairs")
    args = parser.parse_args()

    print("=" * 68)
    print("STAGE 1  load event store")
    frame = pd.read_parquet(STORE)
    if args.limit:
        frame = frame.head(args.limit)
    print(f"  {len(frame)} events  |  {frame['timestamp'].min()} .. {frame['timestamp'].max()}")

    print("STAGE 2  expand events -> per-pair records")
    records = store_df_to_records(frame)
    wanted = {p.strip() for p in args.pairs.split(",") if p.strip()} or set(PAIR_TICKER)
    records = [r for r in records if r["pair"] in wanted]
    pairs = sorted({r["pair"] for r in records})
    print(f"  {len(records)} records across {len(pairs)} pairs: {pairs}")
    if not records:
        print("  no records; abort")
        return

    start = min(r["timestamp"] for r in records)
    end = max(r["timestamp"] for r in records)

    print("STAGE 3  load prices")
    series_by_pair = {}
    for pair in pairs:
        ticker = PAIR_TICKER.get(pair)
        if not ticker:
            continue
        try:
            series_by_pair[pair] = load_price_series(ticker, pair, start, end)
            print(f"  {pair:9s} {len(series_by_pair[pair].times):5d} bars")
        except Exception as exc:
            print(f"  {pair:9s} FAILED: {exc}")

    print("STAGE 4  load market covariates")
    covariates = {}
    for name, ticker in COVARIATES.items():
        try:
            covariates[name] = load_price_series(ticker, name, start, end)
            print(f"  {name:6s} {len(covariates[name].times):5d} bars")
        except Exception as exc:
            print(f"  {name:6s} FAILED: {exc}")
    context_fn = build_market_context_fn(covariates, series_by_pair)

    print("STAGE 5  assemble canonical feature table")
    table = build_feature_table(
        records, series_by_pair,
        horizons_minutes=HORIZONS, context_fn=context_fn, max_entry_lag_minutes=4 * 1440,
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    table.to_parquet(OUT, index=False)
    covered = int(table["ret_1440m"].notna().sum()) if "ret_1440m" in table else 0
    print(f"  {table.shape[0]} rows x {table.shape[1]} cols -> {OUT}")
    print(f"  rows with 1d return: {covered}/{len(table)} ({covered / max(1, len(table)):.0%})")

    print("STAGE 6  impact atlas (event-study)")
    events = [
        Event(event_id=str(r["event_id"]), timestamp=r["timestamp"], pair=r["pair"],
              categories=tuple(r.get("categories", ())))
        for r in records
    ]
    observations = align_events(events, series_by_pair, HORIZONS, max_entry_lag_minutes=4 * 1440)
    print(f"  aligned {len(observations)}/{len(events)} events")
    atlas = build_atlas(observations, HORIZONS, mean_adjust=True)
    # Many (category x horizon) cells are tested at once -> control the false discovery rate.
    corrections = correct_atlas(atlas, alpha=0.05, min_n=MIN_CELL_N)

    header = (f"  {'category':<16}{'horizon':>8}{'n':>6}{'meanAR_bps':>12}{'|AR|_bps':>10}"
              f"{'t':>7}{'p':>8}{'q(FDR)':>9}")
    print(header)
    print("  " + "-" * (len(header) - 2))
    survivors = 0
    for category in sorted(atlas):
        impact = atlas[category]
        for horizon in sorted(impact.horizons):
            h = impact.horizons[horizon]
            if h.n < MIN_CELL_N:
                continue
            correction = corrections.get((category, horizon))
            q = correction.q_value if correction else float("nan")
            star = " *" if (correction and correction.significant) else ""
            survivors += 1 if star else 0
            print(f"  {category:<16}{str(horizon // 1440) + 'd':>8}{h.n:>6}"
                  f"{h.mean_return_bps:>12.1f}{h.mean_abs_return_bps:>10.1f}"
                  f"{h.t_stat:>7.2f}{h.p_value:>8.4f}{q:>9.4f}{star}")
    print(f"\n  (* survives Benjamini-Hochberg FDR at q<=0.05; {survivors}/{len(corrections)} cells)")
    print(f"  (cells with n>={MIN_CELL_N} only; AR = vs sample mean of all events)")
    print("=" * 68)


if __name__ == "__main__":
    main()

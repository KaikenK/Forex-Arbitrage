"""Assemble the canonical event-level feature table end-to-end (daily-resolution demo).

Real events (news_stream USD/INR) x yfinance prices x DXY/VIX/oil market context.
Intraday reaction features stay empty until intraday price feeds land; the daily 1d return
populates now, proving the join + interface. Swap in intraday series later, no interface change.

Run: python -m research.build_features_demo
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from research.data import load_events, load_price_series
from research.features import build_feature_table, build_market_context_fn, events_to_records

EVENTS_PATH = (
    Path(__file__).resolve().parents[1].parent
    / "news_stream" / "data" / "usdinr_timestamped_events_targeted_expanded.jsonl"
)
OUT = Path(__file__).resolve().parent / "artifacts" / "features_demo.parquet"
COVARIATES = {"DXY": "DX-Y.NYB", "VIX": "^VIX", "OIL": "CL=F"}


def main() -> None:
    events = load_events(EVENTS_PATH)
    records = events_to_records(events)
    start = min(e.timestamp for e in events)
    end = max(e.timestamp for e in events)
    print(f"{len(events)} events  |  {start.date()} .. {end.date()}")

    series_by_pair = {"USD/INR": load_price_series("USDINR=X", "USD/INR", start, end)}

    covariates = {}
    for name, ticker in COVARIATES.items():
        try:
            covariates[name] = load_price_series(ticker, name, start, end)
            print(f"context {name} ({ticker}): {len(covariates[name].times)} bars")
        except Exception as exc:
            print(f"context {name} failed: {exc}")
    context_fn = build_market_context_fn(covariates, series_by_pair)

    table = build_feature_table(
        records, series_by_pair,
        horizons_minutes=[1440], context_fn=context_fn, max_entry_lag_minutes=3 * 1440,
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    table.to_parquet(OUT, index=False)
    print(f"\nfeature table: {table.shape[0]} rows x {table.shape[1]} cols -> {OUT}")
    print("columns:", list(table.columns))
    have_ret = table["ret_1440m"].notna().sum() if "ret_1440m" in table else 0
    print(f"rows with 1d return: {have_ret}/{len(table)}")


if __name__ == "__main__":
    main()

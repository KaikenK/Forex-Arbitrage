"""Lead-lag profile: where does the USD/INR reaction to US surprises actually land?

A null result at a single horizon is ambiguous — it can mean "no reaction" or "we measured
the wrong bar". USD/INR is the ambiguous case: US releases print at 13:30 UTC, but India's
onshore session closes around 10:00 UTC, so a daily close stamped at India time would end
*before* the release. The reaction would then appear one bar later, and measuring bar 0 would
find nothing no matter how strong the effect.

This estimates the surprise-response beta at offsets -2..+3 trading bars around each release.

  - Offsets < 0 are **placebos**: a real reaction cannot precede its release. A significant
    negative-offset beta means leakage or misalignment, and invalidates the pipeline.
  - The offset carrying the reaction identifies the correct measurement bar.
  - All offsets null is genuine evidence of no detectable daily-horizon reaction.

R1 re-run (RESEARCH_TRACKER §6, 2026-10-24). The original R1 pooled *raw* surprises, so
"higher is bad" releases (unemployment rate, claims) cancelled "higher is good" ones. The
default now orients both sides: surprise x news_sign (good news > 0) and the return as the
USD's return, so beta > 0 means "good US news strengthens the dollar" in every pair. The
store now carries Option A σ (pandemic window excluded from σ); --exclude-pandemic-window
drops in-window releases (pre-registered robustness). --legacy restores the original raw,
unoriented design; with --store pointing at the pre-fix backup it reproduces R1.

Run: python -m research.run_inr_leadlag [--pair EUR/USD --ticker EURUSD=X] [--exclude-pandemic-window]
     python -m research.run_inr_leadlag --legacy --store research/artifacts/calendar_events.parquet.bak-20261024-173354
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from research.data.prices_yf import load_price_series
from research.eventstudy.sign_conventions import currency_side, news_sign
from research.eventstudy.stats import benjamini_hochberg
from research.eventstudy.surprise import surprise_response

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
CALENDAR_STORE = ARTIFACTS / "calendar_events.parquet"
PAIR, PAIR_TICKER = "USD/INR", "USDINR=X"
SURPRISE_CLIP = 5.0
OFFSETS = (-2, -1, 0, 1, 2, 3)


def build_leadlag_panel(store_path: Path, start, end,
                        pair: str = PAIR, ticker: str = PAIR_TICKER, *,
                        orient: bool = True, exclude_pandemic_window: bool = False) -> pd.DataFrame:
    calendar = pd.read_parquet(store_path)
    sample = calendar[
        (calendar["currencies"] == "USD")
        & (calendar["importance"] == "high")
        & (calendar["timestamp_precision_minutes"] == 0)
        & (calendar["standardized_surprise"].notna())
    ].copy()
    if exclude_pandemic_window:
        if "in_pandemic_window" not in sample.columns:
            raise SystemExit("store has no in_pandemic_window column; rebuild it with ingest_calendar")
        sample = sample[~sample["in_pandemic_window"].astype(bool)]
    sample["timestamp"] = pd.to_datetime(sample["timestamp"], utc=True)
    side = currency_side(pair, "USD") if orient else 1
    if side is None:
        raise SystemExit(f"{pair} has no USD side to orient")

    series = load_price_series(ticker, pair, start, end)
    prices = pd.Series(series.prices, index=pd.DatetimeIndex(series.times)).sort_index()
    returns = prices.pct_change()
    index = returns.index

    rows = []
    for row in sample.to_dict("records"):
        event_time = row["timestamp"]
        position = index.searchsorted(event_time, side="right")  # first bar after the release
        if position <= 2 or position >= len(index) - max(OFFSETS) - 1:
            continue
        sign = news_sign(str(row.get("country") or "United States"), row["event_name"]) if orient else 1
        if sign is None:
            continue  # ambiguous for the currency: excluded from pooled estimates, never guessed
        record = {
            "event_id": row["event_id"],
            "event_name": row["event_name"],
            "release_date": event_time.date(),
            "surprise": sign * float(np.clip(row["standardized_surprise"], -SURPRISE_CLIP, SURPRISE_CLIP)),
        }
        for offset in OFFSETS:
            value = returns.iloc[position + offset]
            record[f"ret_{offset}"] = side * float(value) if np.isfinite(value) else np.nan
        rows.append(record)
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Lead-lag reaction profile around US surprises.")
    parser.add_argument("--store", type=Path, default=CALENDAR_STORE)
    parser.add_argument("--pair", default=PAIR)
    parser.add_argument("--ticker", default=PAIR_TICKER,
                        help="Control pairs (e.g. EURUSD=X) test whether the measurement "
                             "chain places the reaction where theory says it should be.")
    parser.add_argument("--legacy", action="store_true",
                        help="Original R1 design: raw surprise, raw pair return, no orientation.")
    parser.add_argument("--exclude-pandemic-window", action="store_true",
                        help="Option A robustness: drop releases inside 2020-03-01..2021-06-30.")
    parser.add_argument("--json", type=Path, default=None, help="Append this run's results here.")
    args = parser.parse_args()

    start, end = datetime(2018, 1, 1, tzinfo=UTC), datetime(2025, 5, 1, tzinfo=UTC)
    panel = build_leadlag_panel(args.store, start, end, args.pair, args.ticker,
                                orient=not args.legacy,
                                exclude_pandemic_window=args.exclude_pandemic_window)
    design = "legacy (raw, unoriented)" if args.legacy else "sign-oriented (USD's return per good-news sigma)"

    print("=" * 76)
    print(f"{args.pair} lead-lag profile around US macro surprises")
    print(f"design: {design}; pandemic window {'EXCLUDED' if args.exclude_pandemic_window else 'included'}")
    print("=" * 76)
    print(f"\n{len(panel)} releases, {panel['release_date'].nunique()} distinct dates\n")
    print(f"beta = basis points of {'the USD' if not args.legacy else args.pair} return per +1 sd of US surprise")
    print("offsets below 0 are placebos: a reaction cannot precede its release\n")

    header = f"  {'offset':<12}{'n':>6}{'clust':>7}{'beta':>10}{'se':>9}{'t':>8}{'p':>9}"
    print(header)
    print("  " + "-" * (len(header) - 2))

    results = []
    for offset in OFFSETS:
        column = f"ret_{offset}"
        subset = panel[panel[column].notna()]
        result = surprise_response(
            subset[column], subset["surprise"], clusters=subset["release_date"].values
        )
        if result is None:
            continue
        label = f"bar {offset:+d}" + ("  (placebo)" if offset < 0 else "")
        print(f"  {label:<12}{result.n:>6}{result.n_clusters:>7}"
              f"{result.beta * 1e4:>10.2f}{result.beta_se * 1e4:>9.2f}"
              f"{result.beta_t:>8.2f}{result.beta_p:>9.4f}")
        results.append((offset, result))

    corrections = benjamini_hochberg([r.beta_p for _, r in results], alpha=0.05)
    print("\n  Benjamini-Hochberg across offsets:")
    survivors = []
    for (offset, result), correction in zip(results, corrections):
        flag = " *" if correction.significant else ""
        print(f"    bar {offset:+d}   p={correction.p_value:.4f}  q={correction.q_value:.4f}{flag}")
        if correction.significant:
            survivors.append(offset)

    print("\n  Interpretation:")
    placebos = [offset for offset in survivors if offset < 0]
    if placebos:
        print(f"    FAIL: placebo bar(s) {placebos} are significant. The pipeline is "
              "misaligned or leaking; findings are not reportable until this is fixed.")
    elif survivors:
        print(f"    Reaction lands at bar(s) {survivors}; placebos are clean.")
    else:
        print("    No offset survives correction, and placebos are clean. This is a genuine")
        print("    null at daily resolution, not a measurement artifact.")

    # Magnitude at bar 0, as in the original R1 table: |return| on |surprise|.
    bar0 = panel[panel["ret_0"].notna()]
    magnitude = surprise_response(bar0["ret_0"].abs(), bar0["surprise"].abs(),
                                  clusters=bar0["release_date"].values)
    if magnitude is not None:
        print(f"\n  magnitude bar 0 (|ret| on |surprise|): beta={magnitude.beta * 1e4:+.2f} bps  "
              f"p={magnitude.beta_p:.4f}  n={magnitude.n}")
    print("=" * 76)

    if args.json:
        record = {
            "pair": args.pair, "ticker": args.ticker, "store": str(args.store), "design": design,
            "exclude_pandemic_window": args.exclude_pandemic_window,
            "releases": int(len(panel)), "release_dates": int(panel["release_date"].nunique()),
            "offsets": [
                {"offset": offset, "n": result.n, "clusters": result.n_clusters,
                 "beta_bps": result.beta * 1e4, "se_bps": result.beta_se * 1e4, "p": result.beta_p,
                 "q_bh": correction.q_value}
                for (offset, result), correction in zip(results, corrections)
            ],
            "magnitude_bar0": None if magnitude is None else
                {"beta_bps": magnitude.beta * 1e4, "p": magnitude.beta_p, "n": magnitude.n},
        }
        existing = json.loads(args.json.read_text(encoding="utf-8")) if args.json.exists() else []
        existing.append(record)
        args.json.write_text(json.dumps(existing, indent=2, default=float), encoding="utf-8")


if __name__ == "__main__":
    main()

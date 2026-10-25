"""USD/INR event study: how the rupee reacts to US macro surprises.

USD/INR is the project's primary pair. India has no free scheduled-release calendar with a
consensus forecast, and no free intraday INR price history exists, so the causal arm is built
from **US** releases instead: USD/INR is a USD pair, US release timing is scheduled, and the
surprise is measured against a pre-release consensus, so the shock is exogenous to India.

Design:
  - Sample: US high-impact releases with an exact timestamp and a standardized surprise.
  - Response: next-day USD/INR return (daily resolution; see RESEARCH_TRACKER for why).
  - Abnormal: raw return, and the residual after controlling for DXY / oil / US 10y.
  - Inference: surprise-response regression with standard errors clustered by release date,
    plus Benjamini-Hochberg FDR across the per-category tests.

Run: python -m research.run_inr_study
"""
from __future__ import annotations

import argparse
from datetime import UTC, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from research.data.prices_yf import load_price_series
from research.eventstudy.stats import benjamini_hochberg
from research.eventstudy.surprise import surprise_response

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
CALENDAR_STORE = ARTIFACTS / "calendar_events.parquet"

PAIR, PAIR_TICKER = "USD/INR", "USDINR=X"
COVARIATES = {"dxy": "DX-Y.NYB", "oil": "CL=F", "us10y": "^TNX"}
SURPRISE_CLIP = 5.0   # winsorize standardized surprise; the raw tails reach +/-24 sigma


def _series_returns(series) -> pd.Series:
    """Daily simple returns indexed by bar time."""
    frame = pd.Series(series.prices, index=pd.DatetimeIndex(series.times)).sort_index()
    return frame.pct_change().dropna()


def build_panel(store_path: Path, start, end) -> pd.DataFrame:
    calendar = pd.read_parquet(store_path)
    sample = calendar[
        (calendar["currencies"] == "USD")
        & (calendar["importance"] == "high")
        & (calendar["timestamp_precision_minutes"] == 0)
        & (calendar["standardized_surprise"].notna())
    ].copy()
    sample["timestamp"] = pd.to_datetime(sample["timestamp"], utc=True)

    pair_returns = _series_returns(load_price_series(PAIR_TICKER, PAIR, start, end))
    covariate_returns = {
        name: _series_returns(load_price_series(ticker, name, start, end))
        for name, ticker in COVARIATES.items()
    }

    rows = []
    for row in sample.to_dict("records"):
        event_time = row["timestamp"]
        # Leak-safe: the first daily bar strictly *after* the release carries the reaction.
        future = pair_returns.index[pair_returns.index > event_time]
        if len(future) == 0:
            continue
        bar_time = future[0]
        if bar_time - event_time > timedelta(days=4):
            continue  # long gap (holiday); the bar no longer reflects this release
        record = {
            "event_id": row["event_id"],
            "event_name": row["event_name"],
            "categories": row["categories"],
            "timestamp": event_time,
            "release_date": event_time.date(),
            "surprise": float(np.clip(row["standardized_surprise"], -SURPRISE_CLIP, SURPRISE_CLIP)),
            "ret": float(pair_returns.loc[bar_time]),
        }
        for name, series in covariate_returns.items():
            aligned = series.index[series.index == bar_time]
            record[name] = float(series.loc[aligned[0]]) if len(aligned) else np.nan
        rows.append(record)

    return pd.DataFrame(rows)


def _report(label: str, result, unit: float = 1e4) -> dict | None:
    if result is None:
        print(f"  {label:<34}{'insufficient data':>38}")
        return None
    low, high = result.beta_ci95
    print(f"  {label:<30}{result.n:>6}{result.n_clusters:>7}"
          f"{result.beta * unit:>10.2f}{result.beta_se * unit:>9.2f}"
          f"{low * unit:>9.2f}{high * unit:>8.2f}{result.beta_p:>9.4f}")
    return {"label": label, "p": result.beta_p, "beta_bps": result.beta * unit}


def main() -> None:
    parser = argparse.ArgumentParser(description="USD/INR reaction to US macro surprises.")
    parser.add_argument("--store", type=Path, default=CALENDAR_STORE)
    args = parser.parse_args()

    from datetime import datetime
    start, end = datetime(2018, 1, 1, tzinfo=UTC), datetime(2025, 5, 1, tzinfo=UTC)

    print("=" * 78)
    print("USD/INR reaction to US macro surprises (daily resolution)")
    print("=" * 78)
    panel = build_panel(args.store, start, end)
    print(f"\nPanel: {len(panel)} release-observations, "
          f"{panel['release_date'].nunique()} distinct release dates")
    print(f"  {panel['timestamp'].min().date()} .. {panel['timestamp'].max().date()}")
    print(f"  surprise winsorized at +/-{SURPRISE_CLIP} sigma; "
          f"mean {panel['surprise'].mean():+.3f}, sd {panel['surprise'].std():.3f}")

    header = (f"  {'model':<30}{'n':>6}{'clust':>7}{'beta':>10}{'se':>9}"
              f"{'ci_lo':>9}{'ci_hi':>8}{'p':>9}")
    print("\nbeta = USD/INR return in basis points per +1 sd of US surprise")
    print("(positive beta => stronger US data weakens the rupee)")
    print(header)
    print("  " + "-" * (len(header) - 2))

    clusters = panel["release_date"].values
    raw = surprise_response(panel["ret"], panel["surprise"], clusters=clusters)
    _report("raw return", raw)

    controls = {name: panel[name].fillna(0.0).values for name in COVARIATES}
    adjusted = surprise_response(
        panel["ret"], panel["surprise"], clusters=clusters, controls=controls
    )
    _report("abnormal (DXY/oil/US10y)", adjusted)
    if adjusted is not None:
        loadings = ", ".join(f"{k}={v:+.3f}" for k, v in adjusted.control_betas.items())
        print(f"  {'':<30}control loadings: {loadings}")

    print("\nBy event category:")
    print(header)
    print("  " + "-" * (len(header) - 2))
    category_results = []
    exploded = panel.assign(category=panel["categories"].str.split(";")).explode("category")
    exploded = exploded[exploded["category"].notna() & (exploded["category"] != "")]
    for category, group in exploded.groupby("category"):
        if len(group) < 30:
            continue
        result = surprise_response(
            group["ret"], group["surprise"], clusters=group["release_date"].values
        )
        reported = _report(category, result)
        if reported:
            category_results.append(reported)

    if category_results:
        corrections = benjamini_hochberg([r["p"] for r in category_results], alpha=0.05)
        print("\n  Benjamini-Hochberg across categories:")
        for reported, correction in zip(category_results, corrections):
            flag = " *" if correction.significant else ""
            print(f"    {reported['label']:<28}p={correction.p_value:.4f}  "
                  f"q={correction.q_value:.4f}{flag}")

    print("\nBy individual release (>=40 observations):")
    print(header)
    print("  " + "-" * (len(header) - 2))
    for event_name, group in panel.groupby("event_name"):
        if len(group) < 40:
            continue
        _report(event_name[:28],
                surprise_response(group["ret"], group["surprise"],
                                  clusters=group["release_date"].values))
    print("=" * 78)


if __name__ == "__main__":
    main()

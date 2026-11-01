"""Prospective test of the deployed semantic model on its own logged live predictions.

Rule fixed in RESEARCH_TRACKER §2 (RQ1–RQ5, "Prospective test", 2026-11-02) before any outcome
was joined:
  rows       every `live_predictions.jsonl` row with published_at in [--since, --until), a
             non-zero direction and a real (non-synthetic, C27) provider (V1: `direction`; release model: `release_direction`, --basis release)
  outcome    log return of the pair from the first Yahoo hourly bar opening at/after published_at
             to the first bar at/after published_at + horizon_minutes; rows whose exit bar does
             not exist yet are dropped
  metrics    sign accuracy vs 50% and Spearman IC of market_score, clustered by UTC date;
             reliability of the confidence field
Descriptive only: few date clusters.

Run: python -m research.score_live_predictions --predictions <jsonl> [--basis v1|release]
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from research.reaction import metrics

TICKERS = {"EUR/USD": "EURUSD=X", "GBP/USD": "GBPUSD=X", "USD/JPY": "JPY=X", "USD/CHF": "CHF=X", "USD/INR": "INR=X"}
DEFAULT_SINCE = datetime(2026, 10, 25, tzinfo=UTC)
ARTIFACTS = Path(__file__).resolve().parent / "artifacts"


def load_rows(path: Path, *, since: datetime, until: datetime, basis: str) -> pd.DataFrame:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            rows.append(row)
    frame = pd.DataFrame(rows)
    frame["published_at"] = pd.to_datetime(frame["published_at"], utc=True, errors="coerce", format="ISO8601")
    frame = frame[(frame["published_at"] >= since) & (frame["published_at"] < until) & frame["pair"].isin(TICKERS)]
    # C27: synthetic demo headlines are not articles (92% of rows before the 2026-11-02 fix).
    frame = frame[frame.get("provider", pd.Series(index=frame.index, dtype=object)).astype(str).str.lower() != "synthetic"]
    if basis == "release":
        frame = frame[frame.get("basis", pd.Series(index=frame.index, dtype=object)) == "release"]
        frame = frame.assign(signal=pd.to_numeric(frame["release_direction"], errors="coerce"),
                             prob=pd.to_numeric(frame.get("p_direction"), errors="coerce"))
    else:
        frame = frame.assign(signal=pd.to_numeric(frame["direction"], errors="coerce"),
                             prob=pd.to_numeric(frame["confidence"], errors="coerce"))
    frame = frame.assign(horizon=pd.to_numeric(frame["horizon_minutes"], errors="coerce"))
    return frame[(frame["signal"].fillna(0) != 0) & frame["horizon"].notna()].reset_index(drop=True)


def hourly_bars(pair: str, start: datetime, end: datetime) -> pd.Series:
    import yfinance as yf

    data = yf.download(TICKERS[pair], start=start.date().isoformat(), end=end.date().isoformat(),
                       interval="1h", progress=False, auto_adjust=False)
    if data.empty:
        return pd.Series(dtype=float)
    close = data["Close"]
    close = close.iloc[:, 0] if isinstance(close, pd.DataFrame) else close
    close.index = pd.to_datetime(close.index, utc=True)
    return close.sort_index()


def outcome(bars: pd.Series, start: pd.Timestamp, horizon_min: float) -> float:
    """Bars are indexed by their OPEN time; entry/exit = first bar opening at/after the time."""
    times = bars.index
    i = times.searchsorted(start, "left")
    j = times.searchsorted(start + pd.Timedelta(minutes=float(horizon_min)), "left")
    if i >= len(times) or j >= len(times) or j <= i:
        return float("nan")
    return float(np.log(bars.iloc[j] / bars.iloc[i]) * 1e4)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--since", type=lambda s: datetime.fromisoformat(s).astimezone(UTC), default=DEFAULT_SINCE)
    parser.add_argument("--until", type=lambda s: datetime.fromisoformat(s).astimezone(UTC),
                        default=datetime.now(UTC))
    parser.add_argument("--basis", choices=["v1", "release"], default="v1")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    frame = load_rows(args.predictions, since=args.since, until=args.until, basis=args.basis)
    end = datetime.now(UTC)
    returns = np.full(len(frame), np.nan)
    for pair, idx in frame.groupby("pair").groups.items():
        bars = hourly_bars(pair, args.since, end + pd.Timedelta(days=1))
        for k in idx:
            returns[k] = outcome(bars, frame.at[k, "published_at"], frame.at[k, "horizon"])
    frame["outcome_bps"] = returns
    scored = frame[np.isfinite(frame["outcome_bps"]) & (frame["outcome_bps"] != 0)].reset_index(drop=True)
    sign = scored["signal"].to_numpy(dtype=float)
    real = scored["outcome_bps"].to_numpy()
    dates = scored["published_at"].dt.strftime("%Y-%m-%d").to_numpy()
    hit = (np.sign(sign) == np.sign(real)).astype(float)
    test = metrics.clustered_mean_diff(hit - 0.5, dates)
    result = {
        "rule": "RESEARCH_TRACKER §2 RQ1–RQ5 prospective test (2026-11-02)", "basis": args.basis,
        "since": args.since.isoformat(), "until": args.until.isoformat(), "computed_at": datetime.now(UTC).isoformat(),
        "rows_logged": int(len(frame)), "rows_scored": int(len(scored)), "date_clusters": int(len(set(dates))),
        "events": int(scored["event_id"].nunique()) if "event_id" in scored else None,
        "accuracy": float(hit.mean()) if len(hit) else None,
        "accuracy_ci95": metrics.cluster_bootstrap_ci(dates, lambda i: float(hit[i].mean())),
        "p_vs_coin": None if test is None else test["p"],
        "ic_spearman": metrics.spearman(pd.to_numeric(scored["market_score"], errors="coerce").to_numpy(dtype=float), real),
        "by_pair": {p: float(hit[(scored["pair"] == p).to_numpy()].mean()) for p in sorted(scored["pair"].unique())},
        "reliability": metrics.reliability(scored["prob"].to_numpy(dtype=float), hit),
        "ece": metrics.ece(scored["prob"].to_numpy(dtype=float), hit),
    }
    out = args.out or ARTIFACTS / f"live_predictions_score_{args.basis}.json"
    out.write_text(json.dumps(result, indent=2, default=float), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("rows_scored", "date_clusters", "accuracy", "accuracy_ci95",
                                              "p_vs_coin", "ic_spearman")}, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

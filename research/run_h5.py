"""Run the pre-registered H5 test (RESEARCH_TRACKER §2, LOCKED PROTOCOL §6 2026-10-25) — one
index definition, two backends.

Refuses to run without the frozen sample (`freeze_gdelt_sample.py`, hash-verified) and scores
produced on it; events come only from the frozen shards. Same-day framing: results *explain*
moves over the 24 h ending 21:00 UTC; they are not forecasts.

  python -m research.run_h5 --backend lexicon
  python -m research.run_h5 --backend finbert
  python -m research.run_h5 --decide

Inputs: the consolidated GDELT store (headlines + currency tags), `archive_scores/dedup`
(only canonical headlines count), `archive_scores/<backend>` (polarity), the calendar store
(Option A standardized surprises), and yfinance daily closes for the five pairs and ^VIX.
Writes `artifacts/h5_results.json` and the fitted market component, for the live service.

Primary estimates use the Option A surprise index. The pre-registered robustness check
(§6, 2026-10-24) repeats every estimate with pandemic-window releases dropped from the index.
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from research.data.prices_yf import load_price_series
from research.freeze_gdelt_sample import load_freeze, load_frozen_events
from research.score_archive import load_scores, require_frozen_scores
from research.semantic.currency_index import (
    H5_PAIRS,
    STUDY_CURRENCIES,
    ScoredHeadline,
    daily_currency_sentiment,
    fit_market_component,
    implied_pair_sentiment,
    pair_tagged_sentiment,
    surprise_index,
    walk_forward_market_component,
)
from research.semantic.h5 import SENTIMENT_BACKENDS, backend_verdict, h5_decision, h5_test_a, h5_test_b

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
CALENDAR = ARTIFACTS / "calendar_events.parquet"
RESULTS = ARTIFACTS / "h5_results.json"
TICKERS = {"EUR/USD": "EURUSD=X", "GBP/USD": "GBPUSD=X", "USD/JPY": "USDJPY=X",
           "USD/CHF": "USDCHF=X", "USD/INR": "USDINR=X", "VIX": "^VIX"}


def _headlines(backend: str) -> tuple[list[ScoredHeadline], str]:
    freeze = load_freeze()
    store = load_frozen_events(freeze)[["event_id", "timestamp", "currencies"]]
    for stage in ("dedup", backend):
        require_frozen_scores(stage, freeze["sha256"])
    dedup = load_scores("dedup")
    scores = load_scores(backend)
    if dedup.empty or scores.empty:
        raise SystemExit(f"missing scores; run score_archive --stage dedup and --stage {backend}")
    frame = (store.assign(event_id=store["event_id"].astype(str))
             .merge(dedup[dedup["canonical"]][["event_id"]], on="event_id")
             .merge(scores[["event_id", "polarity"]], on="event_id"))
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    headlines = [ScoredHeadline(timestamp=r.timestamp.to_pydatetime(),
                                currencies=tuple(c for c in str(r.currencies).split(";") if c),
                                polarity=float(r.polarity), event_id=r.event_id)
                 for r in frame.itertuples()]
    return headlines, freeze["sha256"]


def _daily_closes(start: datetime, end: datetime) -> pd.DataFrame:
    closes = {}
    for name, ticker in TICKERS.items():
        series = load_price_series(ticker, name, start, end)
        closes[name] = pd.Series(series.prices, index=[t.date() for t in series.times])
    return pd.DataFrame(closes).sort_index()


def _summarise(test_a, test_b) -> dict:
    c = test_a.comparison
    out = {"a_passed": test_a.passed, "b_passed": test_b.passed, "verdict": backend_verdict(test_a, test_b)}
    if c is not None:
        out["a"] = {"n_test": c.n_test, "oos_r2_tagged": c.oos_r2_a, "oos_r2_implied": c.oos_r2_b,
                    "spearman_tagged": c.spearman_a, "spearman_implied": c.spearman_b,
                    "loss_diff_p": c.loss_diff_p}
    if test_a.implied_ols is not None:
        coef = test_a.implied_ols.coefficients["implied_sentiment"]
        out["a"] = {**out.get("a", {}), "implied_beta": coef.beta, "implied_p": coef.p,
                    "n_dates": test_a.implied_ols.n_clusters}
    out["b"] = {}
    for outcome, result in test_b.regressions.items():
        coef = result.coefficients["market_component"] if result else None
        out["b"][outcome] = None if coef is None else {
            "beta": coef.beta, "se": coef.se, "p": coef.p, "n": result.n,
            "passes": test_b.outcome_passes(outcome)}
    return out


def run_backend(backend: str) -> dict:
    headlines, freeze_sha = _headlines(backend)
    first = min(h.timestamp for h in headlines)
    last = max(h.timestamp for h in headlines)
    days = [d.date() for d in pd.bdate_range(first.date(), last.date())]
    index = daily_currency_sentiment(headlines, days=days)
    market = walk_forward_market_component(index)
    closes = _daily_closes(first, last)
    returns = closes.pct_change() * 1e4               # bps, keyed by bar date
    calendar = pd.read_parquet(CALENDAR)

    report: dict = {"backend": backend, "headlines": len(headlines), "days": len(days),
                    "freeze_sha256": freeze_sha,
                    "framing": "same-day: explains moves over the 24 h ending 21:00 UTC; not a forecast"}
    for label, exclude in (("primary", False), ("robustness_no_pandemic_releases", True)):
        si = surprise_index(calendar, days, exclude_pandemic_window=exclude)
        rows = []
        for pair in H5_PAIRS:
            base, _, quote = pair.partition("/")
            rows.append(pd.DataFrame({
                "fx_day": pd.to_datetime(days), "pair": pair,
                "ret_bps": returns[pair].reindex(days).to_numpy(),
                "tagged_sentiment": pair_tagged_sentiment(headlines, pair, days=days).to_numpy(),
                "implied_sentiment": implied_pair_sentiment(index, pair).to_numpy(),
                "si_diff": (si[f"SI_{base}"] - si[f"SI_{quote}"]).to_numpy(),
            }))
        test_a = h5_test_a(pd.concat(rows, ignore_index=True))
        daily = pd.DataFrame({
            "fx_day": pd.to_datetime(days),
            "market_component": market["market_component"].to_numpy(),
            "vix_change": closes["VIX"].diff().reindex(days).to_numpy(),
            "ret_USD/JPY": returns["USD/JPY"].reindex(days).to_numpy(),
            "ret_USD/CHF": returns["USD/CHF"].reindex(days).to_numpy(),
            **{f"SI_{c}": si[f"SI_{c}"].to_numpy() for c in STUDY_CURRENCIES},
        })
        test_b = h5_test_b(daily, controls=[f"SI_{c}" for c in STUDY_CURRENCIES])
        report[label] = _summarise(test_a, test_b)

    final_fit = fit_market_component(index[[f"S_{c}" for c in STUDY_CURRENCIES]].to_numpy(), STUDY_CURRENCIES)
    (ARTIFACTS / f"h5_market_component_{backend}.json").write_text(
        json.dumps({**final_fit.to_dict(), "fitted_through": str(days[-1]),
                    "fitted_at": datetime.now(UTC).isoformat()}, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Pre-registered H5 test, one backend per run.")
    parser.add_argument("--backend", choices=SENTIMENT_BACKENDS)
    parser.add_argument("--decide", action="store_true")
    args = parser.parse_args()

    recorded = json.loads(RESULTS.read_text(encoding="utf-8")) if RESULTS.exists() else {}
    if not args.decide:
        if not args.backend:
            parser.error("--backend is required unless --decide")
        recorded[args.backend] = run_backend(args.backend)
        RESULTS.write_text(json.dumps(recorded, indent=2, default=float), encoding="utf-8")
        print(json.dumps(recorded[args.backend], indent=2, default=float))
    verdicts = {name: row["primary"]["verdict"] for name, row in recorded.items()}
    print(f"\nH5 decision over {sorted(verdicts)}: {h5_decision(verdicts)}")


if __name__ == "__main__":
    np.seterr(all="ignore")
    main()

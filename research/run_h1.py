"""Run H1 under the LOCKED PROTOCOL (RESEARCH_TRACKER §6, 2026-10-25). See `semantic/h1.py`.

Refuses to run unless
  - the frozen sample exists and still matches its hash (`freeze_gdelt_sample.py`),
  - every `score_archive` stage it reads was scored on that frozen sample,
  - every clause in `semantic.h1.UNRESOLVED` has been settled by a logged §6 decision.

  python -m research.run_h1 --embedder sentence_transformer    # primary, decides H1
  python -m research.run_h1 --embedder tfidf_svd                # reported, no decision weight
  python -m research.run_h1 --embedder hashing                  # reported, no decision weight
  python -m research.run_h1 --decide

Primary arm: the four majors, intraday target from HistData M1. Secondary arm: USD/INR, absolute
daily return, R2 design (daily aggregation; prior 20-day realized volatility control).
Exclusions apply to analysis rows only; novelty is still measured against all prior canonical
news, including excluded stories, because "recent coverage" is everything that was published.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from research.data.cache_paths import histdata_cache_dir
from research.data.histdata import load_windowed_series
from research.data.tagging import currencies_to_pairs
from research.features import compute_event_semantics, pair_news_currency
from research.freeze_gdelt_sample import load_freeze, load_frozen_events
from research.score_archive import DEFAULT_OUT as SCORES, load_scores, require_frozen_scores
from research.semantic.h1 import (
    EMBEDDING_BACKENDS,
    MAJORS,
    PRIMARY_EMBEDDER,
    SECONDARY_PAIR,
    UNRESOLVED,
    H1Result,
    ProtocolUnresolved,
    event_targets,
    h1_decision,
    h1_locked_test,
    hours_for_events,
    market_wrap_mask,
    prior_window_control,
    release_window_mask,
    require_resolved,
)
from research.semantic.sentiment import LexiconSentiment, SentimentScore

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
CALENDAR = ARTIFACTS / "calendar_events.parquet"
RESULTS = ARTIFACTS / "h1_results.json"


class _Lookup:
    """Precomputed per-headline outputs behind the embedder / scorer interfaces."""

    def __init__(self, by_headline: dict):
        self.by_headline = by_headline

    def fit(self, texts):
        return self

    def encode(self, texts):
        return np.vstack([self.by_headline[t] for t in texts])

    def score_many(self, texts):
        return [self.by_headline[t] for t in texts]


def _headline_of(events: pd.DataFrame) -> pd.Series:
    return events.set_index(events["event_id"].astype(str))["headline"].astype(str)


def _embedder_factory(name: str, events: pd.DataFrame):
    from research.semantic.embeddings import HashingEmbedder, TfidfSvdEmbedder

    if name == "tfidf_svd":
        return lambda: TfidfSvdEmbedder(dimensions=96)
    if name == "hashing":
        return HashingEmbedder
    parts = sorted((SCORES / "embed").glob("part-*.npy"))
    ids = pd.concat([pd.read_parquet(p.with_name(p.name.replace(".npy", ".ids.parquet"))) for p in parts])
    vectors = np.vstack([np.load(p) for p in parts]).astype(np.float64)
    headline_of = _headline_of(events)
    lookup = {headline_of[e]: v for e, v in zip(ids["event_id"], vectors) if e in headline_of.index}
    return lambda: _Lookup(lookup)


def _finbert(events: pd.DataFrame) -> _Lookup:
    scores = load_scores("finbert").set_index("event_id")
    headline_of = _headline_of(events)
    return _Lookup({headline_of[e]: SentimentScore(float(r.polarity), float(r.confidence), 1)
                    for e, r in scores.iterrows() if e in headline_of.index})


def build_records(events: pd.DataFrame, canonical: dict[str, int], pairs: set[str], usd_policy: str) -> pd.DataFrame:
    """Unit: deduplicated GDELT event x study pair it is tagged to."""
    rows = []
    for row in events.itertuples():
        event_id = str(row.event_id)
        if event_id not in canonical:
            continue
        currencies = [c for c in str(row.currencies).split(";") if c]
        for pair in currencies_to_pairs(currencies, usd_policy=usd_policy):
            if pair in pairs:
                rows.append({"event_id": event_id, "timestamp": row.timestamp, "pair": pair,
                             "headline": str(row.headline)})
    frame = pd.DataFrame(rows, columns=["event_id", "timestamp", "pair", "headline"])
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    return frame


def apply_exclusions(records: pd.DataFrame, calendar: pd.DataFrame, *, word_boundaries: bool) -> tuple[pd.DataFrame, dict]:
    """Drop excluded rows and report how many each exclusion removed (and their overlap)."""
    release = release_window_mask(records, calendar)
    wrap = market_wrap_mask(records["headline"].tolist(), word_boundaries=word_boundaries)
    kept = records[~(release | wrap)].reset_index(drop=True)
    counts = {
        "rows_before": len(records), "events_before": int(records["event_id"].nunique()),
        "dropped_release_window": int(release.sum()), "dropped_market_wrap": int(wrap.sum()),
        "dropped_both": int((release & wrap).sum()), "dropped_total": int((release | wrap).sum()),
        "rows_after": len(kept), "events_after": int(kept["event_id"].nunique()),
    }
    return kept, counts


def _epoch(series: pd.Series) -> np.ndarray:
    return series.astype("int64").to_numpy() // 10**9


def attach_intraday_target_and_control(frame: pd.DataFrame, *, rule: str, min_valid_days: int) -> pd.DataFrame:
    """Target and prior-20-day control in bps, per pair, from HistData M1 (bars at close)."""
    frame = frame.copy()
    frame["target"] = np.nan
    frame["control_prior20"] = np.nan
    for pair in MAJORS:
        rows = frame.index[frame["pair"] == pair]
        if len(rows) == 0:
            continue
        times = _epoch(frame.loc[rows, "timestamp"])
        series, missing = load_windowed_series(pair, hours_for_events(times), histdata_cache_dir())
        if missing:
            print(f"  {pair}: HistData years missing {missing} -> those rows fail closed")
        bar_times = np.array([int(t.timestamp()) for t in series.times], dtype=np.int64)
        prices = np.asarray(series.prices, dtype=float)
        frame.loc[rows, "target"] = event_targets(bar_times, prices, times) * 1e4
        frame.loc[rows, "control_prior20"] = prior_window_control(
            bar_times, prices, times, rule=rule, min_valid_days=min_valid_days) * 1e4
        del series, bar_times, prices
    return frame


def _pair_dummies(frame: pd.DataFrame) -> list[str]:
    names = []
    for pair in sorted(frame["pair"].unique())[1:]:
        frame[f"fe_{pair}"] = (frame["pair"] == pair).astype(float)
        names.append(f"fe_{pair}")
    return names


def run_majors(embedder: str, events, canonical, calendar, settings) -> dict:
    records = build_records(events, canonical, set(MAJORS), settings["gdelt_usd_policy"])
    kept, counts = apply_exclusions(records, calendar, word_boundaries=settings["market_wrap_word_boundaries"])
    currencies = sorted({c for c in kept["pair"].map(pair_news_currency) if c})
    semantics = compute_event_semantics(events, embedder_factory=_embedder_factory(embedder, events),
                                        scorer=_finbert(events), currencies=currencies, canonical=canonical)
    kept["news_currency"] = kept["pair"].map(pair_news_currency)
    frame = kept.merge(semantics, on=["event_id", "news_currency"], how="left")
    lexicon = LexiconSentiment().score_many(frame["headline"].tolist())
    frame["abs_sentiment_lexicon"] = [abs(s.polarity) for s in lexicon]
    frame = attach_intraday_target_and_control(frame, rule=settings["control_day_rule"],
                                               min_valid_days=settings["control_min_valid_days"])
    controls = ["control_prior20", *_pair_dummies(frame)]
    months = settings["walk_forward_min_train_months"]
    primary = h1_locked_test(frame, backend=embedder, target="target", controls=controls,
                             baseline=["abs_sentiment"], min_train_months=months)
    robustness = h1_locked_test(frame, backend=embedder, target="target", controls=controls,
                                baseline=["abs_sentiment_lexicon"], min_train_months=months)
    return {"exclusions": counts, "finbert_baseline": primary.to_row(),
            "lexicon_baseline_robustness": robustness.to_row(),
            "rows_with_target": int(frame["target"].notna().sum())}


def run_inr_secondary(embedder: str, events, canonical, calendar, settings) -> dict:
    """R2 design: INR news-days (UTC date), |daily USD/INR return|, prior 20-day rvol control."""
    from research.build_sentiment_panel import join_prices

    records = build_records(events, canonical, {SECONDARY_PAIR}, settings["gdelt_usd_policy"])
    kept, counts = apply_exclusions(records, calendar, word_boundaries=settings["market_wrap_word_boundaries"])
    semantics = compute_event_semantics(events, embedder_factory=_embedder_factory(embedder, events),
                                        scorer=_finbert(events), currencies=["INR"], canonical=canonical)
    kept["news_currency"] = "INR"
    news = kept.merge(semantics, on=["event_id", "news_currency"], how="left")
    news["date"] = news["timestamp"].dt.date
    daily = news.groupby("date").agg(novelty_mean=("novelty", "mean"),
                                     sentiment_abs_mean=("abs_sentiment", "mean")).reset_index()
    panel = join_prices(daily, SECONDARY_PAIR, "USDINR=X")
    panel["target"] = panel["abs_ret"] * 1e4
    panel["timestamp"] = pd.to_datetime(panel["date"]).dt.tz_localize("UTC")
    panel["day_id"] = panel["date"].astype(str)
    result = h1_locked_test(panel, backend=embedder, target="target", controls=["rvol_20d_prior"],
                            baseline=["sentiment_abs_mean"], novelty="novelty_mean", event_col="day_id",
                            min_train_months=settings["walk_forward_min_train_months"])
    return {"exclusions": counts, "news_days": len(panel), "result": result.to_row(),
            "note": "secondary arm; kill rule counted on news-days, the R2 unit"}


def main() -> None:
    parser = argparse.ArgumentParser(description="H1 under the locked protocol, one embedder per run.")
    parser.add_argument("--embedder", choices=EMBEDDING_BACKENDS)
    parser.add_argument("--decide", action="store_true")
    args = parser.parse_args()

    recorded = json.loads(RESULTS.read_text(encoding="utf-8")) if RESULTS.exists() else {}
    if not args.decide:
        if not args.embedder:
            parser.error("--embedder is required unless --decide")
        try:
            require_resolved(UNRESOLVED)
        except ProtocolUnresolved as exc:
            raise SystemExit(str(exc)) from exc
        freeze = load_freeze()
        events = load_frozen_events(freeze)
        stages = ["dedup", "finbert"] + (["embed"] if args.embedder == PRIMARY_EMBEDDER else [])
        for stage in stages:
            require_frozen_scores(stage, freeze["sha256"])
        dedup = load_scores("dedup")
        canonical = dict(zip(dedup.loc[dedup["canonical"], "event_id"].astype(str),
                             dedup.loc[dedup["canonical"], "coverage_count"].astype(int)))
        calendar = pd.read_parquet(CALENDAR)
        recorded[args.embedder] = {
            "freeze_sha256": freeze["sha256"], "settings": dict(UNRESOLVED),
            "majors": run_majors(args.embedder, events, canonical, calendar, UNRESOLVED),
            "usd_inr_secondary": run_inr_secondary(args.embedder, events, canonical, calendar, UNRESOLVED),
        }
        RESULTS.write_text(json.dumps(recorded, indent=2, default=float), encoding="utf-8")
        print(json.dumps(recorded[args.embedder], indent=2, default=float))

    results = {}
    for name, row in recorded.items():
        r = row["majors"]["finbert_baseline"]
        results[name] = H1Result(name, r["n_rows"], r["n_events"], r["n_dates"], r["novelty_beta"],
                                 r["novelty_se"], r["novelty_p"], None, r["verdict"], r["decision_weight"])
    print(f"\nH1 decision (primary embedder {PRIMARY_EMBEDDER} only): {h1_decision(results)}")


if __name__ == "__main__":
    main()

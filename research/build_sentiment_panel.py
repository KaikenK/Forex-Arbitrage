"""Build the daily sentiment/novelty panel — the dataset the H1 test runs on.

Pipeline: news headlines -> syndication dedup -> embeddings -> novelty + sentiment ->
daily aggregation -> join to the pair's daily return and volatility.

Why daily aggregation rather than an event study: GDELT timestamps are quantized to
15-minute buckets, and a single news item's price impact is not separable at daily
resolution (established in RESEARCH_TRACKER, Result R1). Aggregating the day sidesteps
both problems — the question becomes "do days carrying more novel news show larger moves",
which daily bars can answer.

Run: python -m research.build_sentiment_panel --currency INR --pair USD/INR
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from research.data.prices_yf import load_price_series
from research.semantic.dedup import deduplicate
from research.semantic.embeddings import TfidfSvdEmbedder
from research.semantic.novelty import compute_novelty
from research.semantic.sentiment import build_scorer

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
EVENT_STORE = ARTIFACTS / "events.parquet"
DEFAULT_OUT = ARTIFACTS / "sentiment_panel.parquet"


def load_news(store: Path, currency: str) -> pd.DataFrame:
    frame = pd.read_parquet(store)
    mask = frame["currencies"].fillna("").str.contains(currency, na=False)
    news = frame[mask].copy()
    news["timestamp"] = pd.to_datetime(news["timestamp"], utc=True)
    news = news[news["headline"].astype(str).str.len() > 0]
    return news.sort_values("timestamp").reset_index(drop=True)


def build_embedder(name: str, dimensions: int):
    """Resolve an embedding backend. Swapping this is the H1 ablation axis."""
    if name == "sentence_transformer":
        from research.semantic.embeddings import SentenceTransformerEmbedder
        return SentenceTransformerEmbedder()
    return TfidfSvdEmbedder(dimensions=dimensions)


def score_news(
    news: pd.DataFrame, *, sentiment_model: str, embed_dims: int, embedder_name: str
) -> pd.DataFrame:
    headlines = news["headline"].astype(str).tolist()
    timestamps = news["timestamp"].dt.to_pydatetime().tolist()
    event_ids = news["event_id"].astype(str).tolist()

    embedder = build_embedder(embedder_name, embed_dims)
    print(f"  embedder: {embedder.name}")
    embedder.fit(headlines)
    embeddings = embedder.encode(headlines)
    # Diagnostic: if off-diagonal similarities cluster near zero the embedding space cannot
    # discriminate, and any novelty score built on it is uninformative by construction.
    sample = embeddings[: min(300, len(embeddings))]
    similarity = sample @ sample.T
    off_diagonal = similarity[~np.eye(len(sample), dtype=bool)]
    print(f"  pairwise similarity: mean {off_diagonal.mean():+.3f}, "
          f"sd {off_diagonal.std():.3f}, p95 {np.quantile(off_diagonal, 0.95):+.3f}")

    # Collapse syndicated copies first: a wire story reprinted by 30 outlets is one event,
    # and leaving the copies in would make every story look unoriginal to the novelty scorer.
    canonical = deduplicate(
        event_ids, timestamps, headlines, embeddings,
        domains=news["domain"].fillna("").tolist(),
    )
    print(f"  dedup: {len(news)} -> {len(canonical)} canonical stories")

    keep = {c.event_id: c for c in canonical}
    mask = news["event_id"].astype(str).isin(keep)
    news = news[mask].copy()
    news["coverage_count"] = news["event_id"].astype(str).map(
        lambda e: keep[e].coverage_count)
    news["distinct_domains"] = news["event_id"].astype(str).map(
        lambda e: keep[e].distinct_domains)

    kept_positions = np.flatnonzero(mask.to_numpy())
    kept_embeddings = embeddings[kept_positions]

    novelty = compute_novelty(
        news["event_id"].astype(str).tolist(),
        news["timestamp"].dt.to_pydatetime().tolist(),
        kept_embeddings,
    )
    novelty_frame = pd.DataFrame([score.to_row() for score in novelty])
    news = news.merge(novelty_frame, on="event_id", how="left")

    scorer = build_scorer(sentiment_model)
    print(f"  sentiment scorer: {scorer.name}")
    scores = scorer.score_many(news["headline"].astype(str).tolist())
    news["sentiment"] = [s.polarity for s in scores]
    news["sentiment_confidence"] = [s.confidence for s in scores]
    news["abs_sentiment"] = news["sentiment"].abs()
    return news


def aggregate_daily(news: pd.DataFrame) -> pd.DataFrame:
    news = news.copy()
    news["date"] = news["timestamp"].dt.date
    grouped = news.groupby("date")
    panel = pd.DataFrame({
        "news_count": grouped.size(),
        "novelty_mean": grouped["novelty"].mean(),
        "novelty_max": grouped["novelty"].max(),
        "novelty_sum": grouped["novelty"].sum(),
        "sentiment_mean": grouped["sentiment"].mean(),
        "sentiment_abs_mean": grouped["abs_sentiment"].mean(),
        "sentiment_dispersion": grouped["sentiment"].std(),
        "coverage_mean": grouped["coverage_count"].mean(),
        "coverage_max": grouped["coverage_count"].max(),
    })
    return panel.reset_index()


def join_prices(panel: pd.DataFrame, pair: str, ticker: str) -> pd.DataFrame:
    dates = pd.to_datetime(panel["date"])
    start = dates.min().to_pydatetime().replace(tzinfo=UTC)
    end = dates.max().to_pydatetime().replace(tzinfo=UTC)

    series = load_price_series(ticker, pair, start, end)
    prices = pd.Series(series.prices, index=pd.DatetimeIndex(series.times)).sort_index()
    returns = prices.pct_change()

    market = pd.DataFrame({
        "date": [t.date() for t in returns.index],
        "ret": returns.values,
        "abs_ret": np.abs(returns.values),
    }).dropna()
    # Realized-volatility proxy: rolling sd of daily returns, shifted so it is known
    # *before* the day being explained (no lookahead into the target).
    market["rvol_20d_prior"] = market["ret"].rolling(20).std().shift(1)
    market["abs_ret_prior"] = market["abs_ret"].shift(1)

    merged = panel.merge(market, on="date", how="inner")
    return merged.dropna(subset=["abs_ret"]).reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the daily sentiment/novelty panel.")
    parser.add_argument("--store", type=Path, default=EVENT_STORE)
    parser.add_argument("--currency", default="INR")
    parser.add_argument("--pair", default="USD/INR")
    parser.add_argument("--ticker", default="USDINR=X")
    parser.add_argument("--sentiment-model", default="lexicon", choices=["lexicon", "finbert"])
    parser.add_argument("--embed-dims", type=int, default=96)
    parser.add_argument("--embedder", default="tfidf_svd",
                        choices=["tfidf_svd", "sentence_transformer"])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    print(f"Building sentiment panel for {args.pair} ({args.currency} news)")
    news = load_news(args.store, args.currency)
    print(f"  {len(news)} tagged headlines | "
          f"{news['timestamp'].min().date()} .. {news['timestamp'].max().date()}")

    news = score_news(news, sentiment_model=args.sentiment_model,
                      embed_dims=args.embed_dims, embedder_name=args.embedder)
    scored = news["novelty"].notna().sum()
    print(f"  novelty scored: {scored}/{len(news)} "
          f"(rest lack enough prior context in the 7-day window)")

    daily = aggregate_daily(news)
    print(f"  aggregated to {len(daily)} news-days")

    panel = join_prices(daily, args.pair, args.ticker)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(args.out, index=False)
    print(f"\nPanel: {panel.shape[0]} rows x {panel.shape[1]} cols -> {args.out}")

    print("\nFeature summary:")
    cols = ["news_count", "novelty_mean", "novelty_max", "sentiment_mean",
            "sentiment_abs_mean", "coverage_mean", "abs_ret"]
    print(panel[[c for c in cols if c in panel]].describe().T[
        ["count", "mean", "std", "min", "max"]].to_string())

    print("\nRaw correlation with |return| (not yet controlled):")
    for feature in ("novelty_mean", "novelty_max", "sentiment_mean", "sentiment_abs_mean",
                    "news_count", "coverage_mean"):
        if feature in panel:
            valid = panel[[feature, "abs_ret"]].dropna()
            if len(valid) > 10:
                r = valid[feature].corr(valid["abs_ret"])
                print(f"  {feature:<22}{r:+.4f}   (n={len(valid)})")


if __name__ == "__main__":
    main()

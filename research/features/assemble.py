"""Assemble the canonical event-level feature table: one row per (event x affected pair).

Joins event metadata + calendar fields + intraday reaction/vol/persistence features +
leak-safe market context. Abnormal-return columns layer on afterward via
`research.eventstudy.abnormal.add_abnormal_returns` without changing this interface.

Semantic features (M5) join on afterward too: `compute_event_semantics` scores the news
archive per currency stream (dedup -> novelty -> sentiment) and `attach_semantic_features`
joins those scores onto each (event, pair) row through the pair's non-USD currency.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Iterable

import numpy as np
import pandas as pd

from research.data.tagging import currencies_to_pairs
from research.eventstudy.reactions import DEFAULT_HORIZONS, compute_reaction_features
from research.eventstudy.schema import Event, PriceSeries

from .context import MarketContextFn

# Metadata carried straight through onto every row (scalars, passthrough).
_PASSTHROUGH = (
    "event_id", "source", "event_type", "headline", "url", "domain", "sourcecountry",
    "language", "country", "event_name", "importance", "previous", "forecast", "actual",
    "surprise", "standardized_surprise", "scheduled", "sentiment_score",
)


def _to_utc(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        stamp = pd.Timestamp(value).to_pydatetime()
    except (ValueError, TypeError):
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def store_df_to_records(frame: pd.DataFrame) -> list[dict]:
    """Expand a store DataFrame into per-pair records (currencies -> study pairs)."""
    records: list[dict] = []
    for row in frame.to_dict("records"):
        currencies = [c for c in str(row.get("currencies", "")).split(";") if c]
        categories = tuple(c for c in str(row.get("categories", "")).split(";") if c)
        timestamp = _to_utc(row.get("timestamp"))
        if timestamp is None:
            continue
        for pair in currencies_to_pairs(currencies):
            record = {key: row.get(key) for key in _PASSTHROUGH if key in row}
            record.update({"timestamp": timestamp, "pair": pair, "categories": categories})
            records.append(record)
    return records


def events_to_records(events: Iterable[Event]) -> list[dict]:
    return [
        {
            "event_id": event.event_id, "timestamp": event.timestamp, "pair": event.pair,
            "categories": event.categories, "source": event.source, "headline": event.headline,
            "surprise": event.surprise,
        }
        for event in events
    ]


def _surprise_bucket(surprise) -> str | None:
    if surprise is None or (isinstance(surprise, float) and pd.isna(surprise)):
        return None
    if surprise > 0:
        return "beat"
    if surprise < 0:
        return "miss"
    return "inline"


def build_feature_table(
    records: list[dict],
    series_by_pair: dict[str, PriceSeries],
    *,
    horizons_minutes: list[int] = DEFAULT_HORIZONS,
    context_fn: MarketContextFn | None = None,
    max_entry_lag_minutes: int = 10,
) -> pd.DataFrame:
    rows: list[dict] = []
    for record in records:
        pair = record["pair"]
        timestamp = record["timestamp"]
        row: dict = {key: value for key, value in record.items() if key != "categories"}
        row["categories"] = ";".join(record.get("categories", ()))
        row["surprise_bucket"] = _surprise_bucket(record.get("surprise"))

        series = series_by_pair.get(pair)
        if series is not None:
            event = Event(
                event_id=str(record["event_id"]), timestamp=timestamp, pair=pair,
                categories=tuple(record.get("categories", ())),
            )
            features = compute_reaction_features(
                event, series, horizons_minutes=horizons_minutes,
                max_entry_lag_minutes=max_entry_lag_minutes,
            )
            if features is not None:
                reaction = features.to_row()
                reaction.pop("pair", None)  # already present
                row.update(reaction)
                for horizon, value in features.returns.items():
                    row[f"direction_{horizon}m"] = 1 if value > 0 else (-1 if value < 0 else 0)
                    row[f"absret_{horizon}m"] = abs(value)

        if context_fn is not None:
            row.update(context_fn(pair, timestamp))
        rows.append(row)

    return pd.DataFrame(rows)


# ---------------------------------------------------------------- semantic join (M5)

SEMANTIC_COLUMNS: tuple[str, ...] = (
    "novelty", "max_similarity", "topk_similarity", "centroid_similarity",
    "novelty_context_size", "coverage_count", "distinct_domains",
    "sentiment", "abs_sentiment", "sentiment_confidence",
)


def pair_news_currency(pair: str) -> str | None:
    """The currency whose news stream a pair's GDELT row came from: its non-USD side.

    GDELT rows are attributed with usd_policy="none" (a USD-only headline has no pair), so
    every pair row came from news about its non-USD currency.
    """
    base, _, quote = pair.partition("/")
    if base == "USD":
        return quote or None
    if quote == "USD":
        return base or None
    return None


@dataclass(frozen=True)
class _Coverage:
    coverage_count: int
    distinct_domains: int | None


def compute_event_semantics(
    store_frame: pd.DataFrame,
    *,
    embedder_factory: Callable[[], object],
    scorer,
    currencies: Iterable[str],
    lookback_hours: float = 168.0,
    top_k: int = 5,
    dedup_threshold: float = 0.85,
    canonical: Mapping[str, int] | None = None,
) -> pd.DataFrame:
    """Novelty + sentiment for each (canonical event, currency stream).

    The one pre-registered novelty definition (`semantic/novelty.py`): 1 - mean similarity
    to the 5 closest stories in the strictly-prior 7 days, measured *within the currency's
    own news stream* after syndication dedup — the same construction as Result R2.
    Syndicated copies are dropped (the earliest outlet is the canonical event) and carry
    `coverage_count` instead. A fresh embedder is built per stream, so a fitted backend
    (TF-IDF) learns only that stream's vocabulary.

    `canonical` (event_id -> coverage_count) fixes the deduplicated unit from outside — the
    archive-wide dedup stage of `score_archive` — so every embedder is compared on the same
    events. Without it, each stream is deduplicated with the embedder under test.
    """
    from research.semantic.dedup import deduplicate
    from research.semantic.novelty import compute_novelty

    frame = store_frame.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    tags = frame["currencies"].fillna("").astype(str).str.split(";")
    out: list[pd.DataFrame] = []
    for currency in currencies:
        news = frame[tags.map(lambda codes, c=currency: c in codes)]
        news = news[news["headline"].astype(str).str.len() > 0].sort_values("timestamp")
        if canonical is not None:
            news = news[news["event_id"].astype(str).isin(canonical)]
        if news.empty:
            continue
        headlines = news["headline"].astype(str).tolist()
        timestamps = [stamp.to_pydatetime() for stamp in news["timestamp"]]
        event_ids = news["event_id"].astype(str).tolist()
        embedder = embedder_factory()
        embedder.fit(headlines)
        vectors = embedder.encode(headlines)
        if canonical is not None:
            keep = {e: _Coverage(int(canonical[e]), None) for e in event_ids}
        else:
            domains = news["domain"].fillna("").astype(str).tolist() if "domain" in news else None
            keep = {c.event_id: _Coverage(c.coverage_count, c.distinct_domains) for c in deduplicate(
                event_ids, timestamps, headlines, vectors, domains=domains,
                similarity_threshold=dedup_threshold)}
        mask = np.array([event_id in keep for event_id in event_ids])
        kept_ids = [e for e, m in zip(event_ids, mask) if m]
        kept_times = [t for t, m in zip(timestamps, mask) if m]
        kept_headlines = [h for h, m in zip(headlines, mask) if m]
        novelty = {score.event_id: score.to_row() for score in compute_novelty(
            kept_ids, kept_times, vectors[mask], lookback_hours=lookback_hours, top_k=top_k)}
        sentiments = scorer.score_many(kept_headlines)
        rows = []
        for event_id, sentiment in zip(kept_ids, sentiments):
            row = {"event_id": event_id, "news_currency": currency,
                   "coverage_count": keep[event_id].coverage_count,
                   "distinct_domains": keep[event_id].distinct_domains,
                   "sentiment": sentiment.polarity, "abs_sentiment": abs(sentiment.polarity),
                   "sentiment_confidence": sentiment.confidence}
            row.update({k: v for k, v in novelty.get(event_id, {}).items() if k != "event_id"})
            rows.append(row)
        out.append(pd.DataFrame(rows))
    if not out:
        return pd.DataFrame(columns=["event_id", "news_currency", *SEMANTIC_COLUMNS])
    return pd.concat(out, ignore_index=True)


def attach_semantic_features(table: pd.DataFrame, semantics: pd.DataFrame) -> pd.DataFrame:
    """Left-join semantic scores onto (event, pair) rows via the pair's news currency.

    Rows that were syndicated copies, or whose stream had too little prior context for a
    novelty score, keep NaN novelty and drop out of H1 by `dropna`, never by imputation.
    """
    joined = table.drop(columns=[c for c in SEMANTIC_COLUMNS if c in table], errors="ignore").copy()
    joined["news_currency"] = joined["pair"].map(pair_news_currency)
    keyed = semantics.drop_duplicates(["event_id", "news_currency"])
    return joined.merge(keyed, on=["event_id", "news_currency"], how="left")

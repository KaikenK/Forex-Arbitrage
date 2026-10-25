"""A USD-oriented artifact must turn the dollar's predicted move into each pair's own."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from market.artifact_reaction_scorer import ArtifactReactionScorer
from market.reaction_artifact import load_reaction_artifact
from market.schemas import AnalysisContext, ArticleInput
from news.models import SentimentSource, SourceType, UnifiedNewsEvent

HASH_DIMENSION = 16
FEATURES = HASH_DIMENSION + 8


def _payload(orientation: str | None) -> dict[str, object]:
    payload: dict[str, object] = {
        "hash_dimension": HASH_DIMENSION, "k_neighbors": 3, "direction_neutral_threshold": 0.0005,
        "metadata_fields": ["source"], "model_family": "linear", "structured_feature_dimension": 8,
        "direction_classes": [-1, 0, 1],
        # Strongly "up" whatever the input: the label's side wins.
        "direction_intercepts": [-2.0, -1.0, 3.0],
        "direction_coefficients": [[0.0] * FEATURES for _ in range(3)],
        "magnitude_intercept": 0.0004, "magnitude_coefficients": [0.0] * FEATURES,
        "training_rows": [], "weights_only": True, "available_horizons": [60],
        "horizon_row_counts": {"60": 1}, "training_row_count": 1,
    }
    if orientation is not None:
        payload["direction_orientation"] = orientation
    return payload


def _scorer(tmp_path: Path, orientation: str | None) -> ArtifactReactionScorer:
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(_payload(orientation)), encoding="utf-8")
    return ArtifactReactionScorer.from_path(path)


def _context(pairs: list[str], *, body: str | None = None) -> AnalysisContext:
    headline = "US payrolls beat expectations"
    now = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
    event = UnifiedNewsEvent(event_id="e", schema_version=1, source=SourceType.RSS, headline=headline,
                             body=body, published_at=now, symbols=pairs, raw_sentiment=0.0,
                             sentiment_source=SentimentSource.NONE, ingested_at=now)
    return AnalysisContext(article=ArticleInput(article_text=headline, headline=headline), event=event)


def _directions(scorer: ArtifactReactionScorer, pairs: list[str]) -> dict[str, float]:
    return {s.symbol: s.direction for s in asyncio.run(scorer.score(_context(pairs)))}


def test_usd_orientation_flips_pairs_quoted_in_usd(tmp_path: Path) -> None:
    directions = _directions(_scorer(tmp_path, "usd"), ["USD/JPY", "EUR/USD", "GBP/USD", "USD/INR", "EUR/JPY"])
    assert directions["USD/JPY"] == 1.0 and directions["USD/INR"] == 1.0, "USD base: dollar up = pair up"
    assert directions["EUR/USD"] == -1.0 and directions["GBP/USD"] == -1.0, "USD quote: dollar up = pair down"
    assert directions["EUR/JPY"] == 0.0, "no USD leg: no direction rather than an invented one"


def test_legacy_artifacts_keep_pair_orientation(tmp_path: Path) -> None:
    scorer = _scorer(tmp_path, None)
    assert load_reaction_artifact(tmp_path / "artifact.json").direction_orientation == "pair"
    assert _directions(scorer, ["EUR/USD", "USD/JPY"]) == {"EUR/USD": 1.0, "USD/JPY": 1.0}


def test_unknown_orientation_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "artifact.json").write_text(json.dumps(_payload("eur")), encoding="utf-8")
    with pytest.raises(ValueError, match="direction_orientation"):
        load_reaction_artifact(tmp_path / "artifact.json")


def test_headline_repeated_as_body_is_not_counted_twice(tmp_path: Path) -> None:
    """The bridge sends a feed headline as both headline and article_text."""
    scorer = _scorer(tmp_path, "usd")
    once = scorer._query_row(_context(["EUR/USD"]), "EUR/USD", 60).feature_vector
    twice = scorer._query_row(_context(["EUR/USD"], body="US payrolls beat expectations"), "EUR/USD", 60).feature_vector
    assert once == twice

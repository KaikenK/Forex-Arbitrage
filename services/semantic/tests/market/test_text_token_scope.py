"""Pair-group text tokens: the scorer must build exactly the training feature vector."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from market.artifact_reaction_scorer import ArtifactReactionScorer
from market.pair_assets import scoped_text_token, text_token_group
from market.reaction_artifact import load_reaction_artifact
from market.schemas import AnalysisContext, ArticleInput
from news.models import SentimentSource, SourceType, UnifiedNewsEvent
from training.models import MarketReactionTrainingRow
from training.reaction.config import ReactionTrainingConfig
from training.reaction.train import assemble_reaction_features

DIM = 64
METADATA_FIELDS = ["source", "event_category", "macro_event_type"]
HEADLINE = "Oil jumps as Iran tensions lift the dollar"
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def _artifact(tmp_path: Path, scope: str | None) -> Path:
    features = DIM + 8
    payload: dict[str, object] = {
        "hash_dimension": DIM, "k_neighbors": 3, "direction_neutral_threshold": 0.0005,
        "metadata_fields": METADATA_FIELDS, "model_family": "linear", "structured_feature_dimension": 8,
        "direction_classes": [-1, 0, 1], "direction_intercepts": [0.0, 0.0, 0.0],
        "direction_coefficients": [[0.0] * features for _ in range(3)],
        "magnitude_intercept": 0.0, "magnitude_coefficients": [0.0] * features,
        "training_rows": [], "weights_only": True, "available_horizons": [60],
        "horizon_row_counts": {"60": 1}, "direction_orientation": "usd",
    }
    if scope is not None:
        payload["text_token_scope"] = scope
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _training_vector(pair: str, scope: str, tmp_path: Path) -> list[float]:
    row = MarketReactionTrainingRow(
        article_text=HEADLINE, timestamp=NOW, pair=pair, horizon_minutes=60, sentiment_label=0.0,
        reaction_label=0.0, magnitude_label=0.0, source_metadata={"source": "RSS", "event_category": "macro_data"},
        event_category="macro_data",
    )
    config = ReactionTrainingConfig(training_table_path=tmp_path / "unused.jsonl", output_dir=tmp_path,
                                    hash_dimension=DIM, metadata_fields=METADATA_FIELDS, text_token_scope=scope)
    return list(assemble_reaction_features(row, config).feature_vector)


def _serving_vector(pair: str, artifact_path: Path) -> list[float]:
    scorer = ArtifactReactionScorer.from_path(artifact_path)
    event = UnifiedNewsEvent(event_id="e", schema_version=1, source=SourceType.RSS, headline=HEADLINE,
                             body=HEADLINE, published_at=NOW, symbols=[pair], raw_sentiment=0.0,
                             sentiment_source=SentimentSource.NONE, ingested_at=NOW)
    context = AnalysisContext(article=ArticleInput(article_text=HEADLINE, headline=HEADLINE,
                                                   event_category="macro_data"), event=event)
    return list(scorer._query_row(context, pair, 60).feature_vector)


@pytest.mark.parametrize("scope", ["shared", "pair_group"])
@pytest.mark.parametrize("pair", ["USD/INR", "EUR/USD", "USD/JPY"])
def test_serving_features_equal_training_features(tmp_path: Path, scope: str, pair: str) -> None:
    """No train/serve skew: same headline, same pair, same scope -> identical vector."""
    assert _serving_vector(pair, _artifact(tmp_path, scope)) == pytest.approx(_training_vector(pair, scope, tmp_path))


def test_pair_group_separates_inr_text_from_majors_text() -> None:
    assert text_token_group("USD/INR") == "inr"
    assert {text_token_group(p) for p in ("EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF")} == {"majors"}
    assert scoped_text_token("iran", "USD/INR", "pair_group") != scoped_text_token("iran", "EUR/USD", "pair_group")
    assert scoped_text_token("iran", "USD/JPY", "pair_group") == scoped_text_token("iran", "EUR/USD", "pair_group")
    assert scoped_text_token("iran", "USD/INR", "shared") == "iran"


def test_legacy_artifact_defaults_to_shared_and_unknown_scope_is_rejected(tmp_path: Path) -> None:
    assert load_reaction_artifact(_artifact(tmp_path, None)).text_token_scope == "shared"
    bad = _artifact(tmp_path, "per_pair")
    with pytest.raises(ValueError, match="text_token_scope"):
        load_reaction_artifact(bad)

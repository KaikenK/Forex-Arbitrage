"""Weights-only linear artifacts must predict exactly what the full artifact predicts.

Linear inference reads only coefficients, so a linear artifact no longer embeds its
training rows. That kept the file from growing with the dataset (1.35 MB at 133 rows,
~49 MB at 5,000) — but it is only safe if dropping the rows changes no prediction.
These tests pin that, plus the cases where rows must stay.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path

import pytest

PROJECT_SRC = Path(__file__).resolve().parents[2] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.append(str(PROJECT_SRC))

from market.artifact_reaction_scorer import ArtifactReactionScorer
from market.reaction_artifact import load_reaction_artifact
from market.schemas import AnalysisContext
from market.schemas import ArticleInput
from news.models import SentimentSource
from news.models import SourceType
from news.models import UnifiedNewsEvent

HASH_DIMENSION = 32
FEATURE_DIMENSION = HASH_DIMENSION + 8


def _row(event_id: str, horizon: int, direction: int) -> dict[str, object]:
    vector = [0.05 * ((index + horizon) % 7) for index in range(FEATURE_DIMENSION)]
    return {
        "event_id": event_id,
        "pair": "USD/INR",
        "horizon_minutes": horizon,
        "timestamp": "2026-03-20T12:00:00+00:00",
        "direction_label": direction,
        "magnitude_label": 0.0004,
        "feature_vector": vector,
        "horizon_feature_vector": vector,
        "source_metadata": {"source": "RSS"},
    }


def _linear_payload(*, with_rows: bool) -> dict[str, object]:
    rows = [_row("a", 60, 1), _row("b", 60, -1), _row("c", 240, 0), _row("d", 1440, 1)]
    payload: dict[str, object] = {
        "hash_dimension": HASH_DIMENSION,
        "k_neighbors": 3,
        "direction_neutral_threshold": 0.0005,
        "metadata_fields": ["source", "event_category", "macro_event_type", "scheduled"],
        "model_family": "linear",
        "structured_feature_dimension": 8,
        "direction_classes": [-1, 0, 1],
        "direction_intercepts": [-0.1, 0.2, -0.05],
        "direction_coefficients": [
            [0.01 * ((index * 3) % 11) for index in range(FEATURE_DIMENSION)],
            [-0.02 * ((index * 5) % 7) for index in range(FEATURE_DIMENSION)],
            [0.015 * ((index * 7) % 13) for index in range(FEATURE_DIMENSION)],
        ],
        "magnitude_intercept": 0.0003,
        "magnitude_coefficients": [0.00001 * (index % 5) for index in range(FEATURE_DIMENSION)],
        "direction_calibration_temperature": 0.5,
        "training_rows": rows if with_rows else [],
    }
    if not with_rows:
        payload.update(
            weights_only=True,
            training_row_count=len(rows),
            available_horizons=[60, 240, 1440],
            horizon_row_counts={"60": 2, "240": 1, "1440": 1},
        )
    return payload


def _write(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _context(text: str) -> AnalysisContext:
    now = datetime(2026, 3, 21, 9, 0, tzinfo=UTC)
    event = UnifiedNewsEvent(
        event_id="evt",
        schema_version=1,
        source=SourceType.RSS,
        headline=text,
        body=None,
        published_at=now,
        symbols=["USD/INR"],
        raw_sentiment=0.0,
        sentiment_source=SentimentSource.NONE,
        ingested_at=now,
    )
    return AnalysisContext(article=ArticleInput(article_text=text, headline=text), event=event)


HEADLINES = [
    "RBI holds repo rate steady as inflation cools",
    "Rupee slips past 88 as oil climbs and FII outflows persist",
    "US payrolls beat forecasts, dollar strengthens broadly",
    "Fed signals patience on rate cuts amid sticky inflation",
]


@pytest.mark.parametrize("preferred_horizon", [None, 60, 240, 1440])
def test_weights_only_artifact_predicts_identically(tmp_path: Path, preferred_horizon: int | None) -> None:
    full = ArtifactReactionScorer.from_path(
        artifact_path=_write(tmp_path / "full.json", _linear_payload(with_rows=True)),
        preferred_horizon_minutes=preferred_horizon,
    )
    lean = ArtifactReactionScorer.from_path(
        artifact_path=_write(tmp_path / "lean.json", _linear_payload(with_rows=False)),
        preferred_horizon_minutes=preferred_horizon,
    )

    for headline in HEADLINES:
        expected = asyncio.run(full.score(_context(headline)))[0]
        actual = asyncio.run(lean.score(_context(headline)))[0]
        assert (actual.direction, actual.magnitude, actual.confidence, actual.horizon_minutes) == (
            expected.direction,
            expected.magnitude,
            expected.confidence,
            expected.horizon_minutes,
        )


def test_weights_only_artifact_reports_recorded_horizons(tmp_path: Path) -> None:
    artifact = load_reaction_artifact(_write(tmp_path / "lean.json", _linear_payload(with_rows=False)))

    assert artifact.training_rows == []
    assert artifact.available_horizons() == [60, 240, 1440]
    assert artifact.horizon_counts() == {60: 2, 240: 1, 1440: 1}
    assert artifact.training_row_count == 4


def test_knn_artifact_without_rows_is_rejected(tmp_path: Path) -> None:
    """k-NN inference needs the rows, so a row-less k-NN artifact must fail loudly."""
    payload = _linear_payload(with_rows=False)
    payload["model_family"] = "knn"

    with pytest.raises(ValueError, match="training_rows"):
        load_reaction_artifact(_write(tmp_path / "knn.json", payload))


def test_linear_artifact_missing_coefficients_is_rejected(tmp_path: Path) -> None:
    """Weights-only is only allowed when the coefficients are actually present."""
    payload = _linear_payload(with_rows=False)
    del payload["magnitude_coefficients"]

    with pytest.raises(ValueError, match="training_rows"):
        load_reaction_artifact(_write(tmp_path / "broken.json", payload))


def test_legacy_artifact_with_rows_still_loads(tmp_path: Path) -> None:
    """Artifacts exported before this change carry rows and no horizon fields."""
    artifact = load_reaction_artifact(_write(tmp_path / "legacy.json", _linear_payload(with_rows=True)))

    assert len(artifact.training_rows) == 4
    assert artifact.available_horizons() == [60, 240, 1440]
    assert artifact.horizon_counts() == {60: 2, 240: 1, 1440: 1}

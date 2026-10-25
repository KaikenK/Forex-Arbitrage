from __future__ import annotations

import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path

PROJECT_SRC = Path(__file__).resolve().parents[2] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.append(str(PROJECT_SRC))

from training.models import MarketReactionTrainingRow
from training.reaction.config import ReactionTrainingConfig
from training.reaction.train import assemble_reaction_features
from training.reaction.train import load_reaction_rows
from training.reaction.train import train_reaction_model
from training.reaction.train import time_based_split


def make_row(*, minute: int, reaction_label: float) -> MarketReactionTrainingRow:
    return MarketReactionTrainingRow(
        article_text="EUR/USD reacts to CPI surprise",
        timestamp=datetime(2026, 3, 25, 12, minute, tzinfo=UTC),
        pair="EUR/USD",
        horizon_minutes=15,
        sentiment_label=0.5,
        reaction_label=reaction_label,
        magnitude_label=abs(reaction_label),
        source_metadata={
            "source": "rss",
            "event_category": "macro",
            "macro_event_type": "cpi",
            "event_id": f"event-{minute}",
        },
    )


def test_assemble_reaction_features_includes_text_and_metadata(tmp_path: Path) -> None:
    config = ReactionTrainingConfig(
        training_table_path=tmp_path / "rows.jsonl",
        output_dir=tmp_path / "artifacts",
        hash_dimension=32,
    )
    feature_row = assemble_reaction_features(make_row(minute=0, reaction_label=0.01), config)

    assert feature_row.direction_label == 1
    assert feature_row.event_id == "event-0"
    assert feature_row.pair == "EUR/USD"
    assert feature_row.horizon_minutes == 15
    assert len(feature_row.feature_vector) == 40
    assert len(feature_row.horizon_feature_vector) == 40
    assert sum(abs(value) for value in feature_row.feature_vector) > 0


def test_time_based_split_keeps_temporal_order(tmp_path: Path) -> None:
    config = ReactionTrainingConfig(
        training_table_path=tmp_path / "rows.jsonl",
        output_dir=tmp_path / "artifacts",
    )
    feature_rows = [assemble_reaction_features(make_row(minute=index, reaction_label=0.01), config) for index in range(10)]

    train_rows, validation_rows, test_rows = time_based_split(feature_rows, train_ratio=0.6, validation_ratio=0.2)

    assert [row.event_id for row in train_rows] == [f"event-{index}" for index in range(6)]
    assert [row.event_id for row in validation_rows] == ["event-6", "event-7"]
    assert [row.event_id for row in test_rows] == ["event-8", "event-9"]


def test_train_reaction_model_can_fit_linear_artifact(tmp_path: Path) -> None:
    training_table_path = tmp_path / "rows.jsonl"
    output_dir = tmp_path / "artifacts"
    raw_rows = []
    reaction_labels = [0.02, -0.03, 0.0, 0.018, -0.025, 0.0, 0.022, -0.028, 0.0, 0.019, -0.021, 0.0]
    for index, reaction_label in enumerate(reaction_labels):
        raw_rows.append(
            {
                "article_text": f"EUR/USD reacts to macro event {index}",
                "timestamp": datetime(2026, 3, 25, 12, index, tzinfo=UTC).isoformat(),
                "pair": "EUR/USD",
                "horizon_minutes": 15,
                "sentiment_label": 0.1,
                "reaction_label": reaction_label,
                "magnitude_label": abs(reaction_label),
                "source_metadata": {
                    "source": "rss",
                    "event_category": "macro",
                    "macro_event_type": "cpi",
                    "event_id": f"event-{index}",
                },
            },
        )
    training_table_path.write_text("\n".join(__import__("json").dumps(row) for row in raw_rows) + "\n", encoding="utf-8")

    config = ReactionTrainingConfig(
        training_table_path=training_table_path,
        output_dir=output_dir,
        model_family="linear",
        hash_dimension=32,
        train_ratio=0.67,
        validation_ratio=0.16,
    )

    artifact, evaluation = train_reaction_model(config)

    assert artifact.model_family == "linear"
    assert artifact.direction_classes == [-1, 0, 1]
    assert artifact.direction_coefficients is not None
    assert artifact.magnitude_coefficients is not None
    assert artifact.direction_calibration_temperature is not None
    assert isinstance(artifact.reliability_weights, dict)
    assert 0.0 <= evaluation.direction_accuracy <= 1.0


def test_train_reaction_model_learns_horizon_classifier_when_multiple_horizons_exist(tmp_path: Path) -> None:
    training_table_path = tmp_path / "rows_multi_horizon.jsonl"
    output_dir = tmp_path / "artifacts"
    raw_rows = []
    for index in range(18):
        horizon_minutes = 15 if index % 2 == 0 else 60
        reaction_label = 0.02 if horizon_minutes == 15 else -0.015
        raw_rows.append(
            {
                "article_text": f"USD/INR macro event {index} with horizon {horizon_minutes}",
                "timestamp": datetime(2026, 3, 25, 12, index, tzinfo=UTC).isoformat(),
                "pair": "USD/INR",
                "horizon_minutes": horizon_minutes,
                "sentiment_label": 0.1,
                "reaction_label": reaction_label,
                "magnitude_label": abs(reaction_label),
                "scheduled": horizon_minutes == 15,
                "macro_event_type": "cpi" if horizon_minutes == 15 else "rate-decision",
                "source_metadata": {
                    "source": "rss",
                    "event_category": "macro",
                    "macro_event_type": "cpi" if horizon_minutes == 15 else "rate-decision",
                    "event_id": f"event-{index}",
                },
            },
        )
    training_table_path.write_text("\n".join(__import__("json").dumps(row) for row in raw_rows) + "\n", encoding="utf-8")

    config = ReactionTrainingConfig(
        training_table_path=training_table_path,
        output_dir=output_dir,
        model_family="linear",
        hash_dimension=32,
        train_ratio=0.67,
        validation_ratio=0.16,
    )

    artifact, evaluation = train_reaction_model(config)

    assert artifact.horizon_classes == [15, 60]
    assert artifact.horizon_coefficients is not None
    assert evaluation.horizon_prediction is not None
    assert 0.0 <= float(evaluation.horizon_prediction["accuracy"]) <= 1.0

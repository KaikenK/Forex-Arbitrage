from __future__ import annotations

import json
import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path

PROJECT_SRC = Path(__file__).resolve().parents[2] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.append(str(PROJECT_SRC))

from training.reaction.config import ReactionTrainingConfig
from training.reaction.evaluate import ReactionEvaluationResult
from training.reaction.export import export_reaction_artifact
from training.reaction.train import ReactionFeatureRow
from training.reaction.train import ReactionModelArtifact


def test_export_reaction_artifact_preserves_scoreboard_contract(tmp_path: Path) -> None:
    config = ReactionTrainingConfig(
        training_table_path=tmp_path / "rows.jsonl",
        output_dir=tmp_path / "artifact",
        hash_dimension=32,
    )
    artifact = ReactionModelArtifact(
        hash_dimension=32,
        k_neighbors=3,
        direction_neutral_threshold=0.0005,
        metadata_fields=["source", "event_category", "macro_event_type", "scheduled", "standardized_surprise"],
        training_rows=[
            ReactionFeatureRow(
                event_id="evt-1",
                pair="USD/INR",
                horizon_minutes=60,
                timestamp=datetime(2026, 4, 22, 12, 0, tzinfo=UTC),
                direction_label=1,
                magnitude_label=0.01,
                feature_vector=[0.0] * 40,
                horizon_feature_vector=[0.0] * 40,
                source_metadata={"source": "RSS"},
                macro_event_type="rate-decision",
                scheduled=True,
            ),
        ],
        structured_feature_dimension=8,
        direction_calibration_temperature=1.1,
        reliability_weights={"source=rss": 1.05},
    )
    evaluation = ReactionEvaluationResult(
        magnitude_mae=0.001,
        direction_accuracy=0.7,
        direction_macro_f1=0.68,
        confusion_matrix=[[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        calibration_score=0.22,
        pair_breakdown={"USD/INR": {"count": 3.0, "direction_accuracy": 0.7, "magnitude_mae": 0.001}},
        horizon_breakdown={"60": {"count": 3.0, "direction_accuracy": 0.7, "magnitude_mae": 0.001}},
        event_family_breakdown={"rate-decision": {"count": 3.0, "direction_accuracy": 0.7, "magnitude_mae": 0.001}},
        scheduled_breakdown={"scheduled": {"count": 3.0, "direction_accuracy": 0.7, "magnitude_mae": 0.001}},
        horizon_prediction={"accuracy": 0.8, "breakdown": {"60": {"count": 3.0, "accuracy": 0.8}}},
    )

    artifact_path = export_reaction_artifact(artifact, config, evaluation=evaluation)
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))

    assert set(payload["evaluation"].keys()) >= {
        "magnitude_mae",
        "direction_accuracy",
        "direction_macro_f1",
        "calibration_score",
        "pair_breakdown",
        "horizon_breakdown",
        "event_family_breakdown",
        "scheduled_breakdown",
    }
    assert payload["evaluation"]["horizon_prediction"]["accuracy"] == 0.8


def _feature_row(event_id: str, horizon: int) -> ReactionFeatureRow:
    return ReactionFeatureRow(
        event_id=event_id,
        pair="USD/INR",
        horizon_minutes=horizon,
        timestamp=datetime(2026, 4, 22, 12, 0, tzinfo=UTC),
        direction_label=1,
        magnitude_label=0.01,
        feature_vector=[0.0] * 40,
        horizon_feature_vector=[0.0] * 40,
        source_metadata={"source": "RSS"},
    )


def _artifact(model_family: str) -> ReactionModelArtifact:
    linear = model_family == "linear"
    return ReactionModelArtifact(
        hash_dimension=32,
        k_neighbors=3,
        direction_neutral_threshold=0.0005,
        metadata_fields=["source"],
        training_rows=[_feature_row("a", 60), _feature_row("b", 60), _feature_row("c", 240)],
        model_family=model_family,
        structured_feature_dimension=8,
        direction_classes=[-1, 0, 1] if linear else None,
        direction_intercepts=[0.0, 0.0, 0.0] if linear else None,
        direction_coefficients=[[0.0] * 40] * 3 if linear else None,
        magnitude_intercept=0.0 if linear else None,
        magnitude_coefficients=[0.0] * 40 if linear else None,
    )


def _export(tmp_path: Path, artifact: ReactionModelArtifact) -> dict[str, object]:
    config = ReactionTrainingConfig(
        training_table_path=tmp_path / "rows.jsonl",
        output_dir=tmp_path / "artifact",
        hash_dimension=32,
    )
    return json.loads(export_reaction_artifact(artifact, config).read_text(encoding="utf-8"))


def test_linear_artifact_is_exported_weights_only(tmp_path: Path) -> None:
    """Rows are not needed for linear inference, so they must not bloat the file."""
    payload = _export(tmp_path, _artifact("linear"))

    assert payload["weights_only"] is True
    assert payload["training_rows"] == []
    assert payload["training_row_count"] == 3
    assert payload["available_horizons"] == [60, 240]
    assert payload["horizon_row_counts"] == {"60": 2, "240": 1}


def test_knn_artifact_keeps_its_rows(tmp_path: Path) -> None:
    """k-NN predicts from neighbours, so its rows are the model and must be kept."""
    payload = _export(tmp_path, _artifact("knn"))

    assert payload["weights_only"] is False
    assert len(payload["training_rows"]) == 3
    assert payload["available_horizons"] == [60, 240]
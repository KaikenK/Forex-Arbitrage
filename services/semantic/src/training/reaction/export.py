from __future__ import annotations

import json
from pathlib import Path

from .config import ReactionTrainingConfig
from .evaluate import ReactionEvaluationResult
from .train import ReactionModelArtifact

ARTIFACT_FILENAME = "reaction_model_artifact.json"


def export_reaction_artifact(
    artifact: ReactionModelArtifact,
    config: ReactionTrainingConfig,
    evaluation: ReactionEvaluationResult | None = None,
    *,
    extra_payload: dict[str, object] | None = None,
) -> Path:
    """Exports a local market-reaction artifact.

    Linear artifacts are written **weights-only**: inference multiplies features by
    coefficients and never reads the training rows, yet embedding them made the file grow
    linearly with the dataset (1.35 MB at 133 rows, ~50 MB at 5,000) — all of it parsed at
    service startup and mirrored into Arbex. k-NN artifacts still need their rows.
    """
    output_dir = config.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    horizon_row_counts: dict[str, int] = {}
    for row in artifact.training_rows:
        key = str(row.horizon_minutes)
        horizon_row_counts[key] = horizon_row_counts.get(key, 0) + 1
    weights_only = _is_complete_linear(artifact)

    artifact_payload: dict[str, object] = {
        "hash_dimension": artifact.hash_dimension,
        "k_neighbors": artifact.k_neighbors,
        "direction_neutral_threshold": artifact.direction_neutral_threshold,
        "metadata_fields": artifact.metadata_fields,
        "model_family": artifact.model_family,
        "uses_semantic_text_features": artifact.uses_semantic_text_features,
        "structured_feature_dimension": artifact.structured_feature_dimension,
        "direction_orientation": artifact.direction_orientation,
        "text_token_scope": artifact.text_token_scope,
        "weights_only": weights_only,
        "training_row_count": len(artifact.training_rows),
        "available_horizons": sorted({row.horizon_minutes for row in artifact.training_rows}),
        "horizon_row_counts": horizon_row_counts,
        "training_rows": [] if weights_only else [
            {
                "event_id": row.event_id,
                "pair": row.pair,
                "horizon_minutes": row.horizon_minutes,
                "timestamp": row.timestamp.isoformat(),
                "direction_label": row.direction_label,
                "magnitude_label": row.magnitude_label,
                "feature_vector": row.feature_vector,
                "horizon_feature_vector": row.horizon_feature_vector,
                "source_metadata": row.source_metadata,
                "event_category": row.event_category,
                "macro_event_type": row.macro_event_type,
                "scheduled": row.scheduled,
                "actual": row.actual,
                "forecast": row.forecast,
                "previous": row.previous,
                "standardized_surprise": row.standardized_surprise,
                "calendar_source": row.calendar_source,
                "source_url": row.source_url,
            }
            for row in artifact.training_rows
        ],
    }
    if artifact.direction_classes is not None:
        artifact_payload["direction_classes"] = artifact.direction_classes
    if artifact.direction_intercepts is not None:
        artifact_payload["direction_intercepts"] = artifact.direction_intercepts
    if artifact.direction_coefficients is not None:
        artifact_payload["direction_coefficients"] = artifact.direction_coefficients
    if artifact.magnitude_intercept is not None:
        artifact_payload["magnitude_intercept"] = artifact.magnitude_intercept
    if artifact.magnitude_coefficients is not None:
        artifact_payload["magnitude_coefficients"] = artifact.magnitude_coefficients
    if artifact.direction_calibration_temperature is not None:
        artifact_payload["direction_calibration_temperature"] = artifact.direction_calibration_temperature
    if artifact.horizon_classes is not None:
        artifact_payload["horizon_classes"] = artifact.horizon_classes
    if artifact.horizon_intercepts is not None:
        artifact_payload["horizon_intercepts"] = artifact.horizon_intercepts
    if artifact.horizon_coefficients is not None:
        artifact_payload["horizon_coefficients"] = artifact.horizon_coefficients
    if artifact.reliability_weights is not None:
        artifact_payload["reliability_weights"] = artifact.reliability_weights
    artifact_payload["recency_half_life_days"] = artifact.recency_half_life_days
    if evaluation is not None:
        artifact_payload["evaluation"] = {
            "magnitude_mae": evaluation.magnitude_mae,
            "direction_accuracy": evaluation.direction_accuracy,
            "direction_macro_f1": evaluation.direction_macro_f1,
            "confusion_matrix": evaluation.confusion_matrix,
            "calibration_score": evaluation.calibration_score,
            "pair_breakdown": evaluation.pair_breakdown,
            "horizon_breakdown": evaluation.horizon_breakdown,
            "event_family_breakdown": evaluation.event_family_breakdown,
            "scheduled_breakdown": evaluation.scheduled_breakdown,
        }
        if evaluation.horizon_prediction is not None:
            artifact_payload["evaluation"]["horizon_prediction"] = evaluation.horizon_prediction

    for key, value in (extra_payload or {}).items():
        if key in artifact_payload:
            raise ValueError(f"extra_payload may not overwrite artifact field {key!r}.")
        artifact_payload[key] = value

    artifact_path = output_dir / ARTIFACT_FILENAME
    artifact_path.write_text(json.dumps(artifact_payload, indent=2), encoding="utf-8")
    return artifact_path


def _is_complete_linear(artifact: ReactionModelArtifact) -> bool:
    """Mirror of the runtime check: a linear model with every coefficient present."""
    return (
        artifact.model_family == "linear"
        and artifact.direction_classes is not None
        and artifact.direction_intercepts is not None
        and artifact.direction_coefficients is not None
        and artifact.magnitude_intercept is not None
        and artifact.magnitude_coefficients is not None
    )

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class RuntimeReactionRow:
    event_id: str
    pair: str
    horizon_minutes: int
    timestamp: datetime
    direction_label: int
    magnitude_label: float
    feature_vector: list[float]
    horizon_feature_vector: list[float]
    source_metadata: dict[str, str]
    event_category: str | None = None
    macro_event_type: str | None = None
    scheduled: bool | None = None
    actual: float | None = None
    forecast: float | None = None
    previous: float | None = None
    standardized_surprise: float | None = None
    calendar_source: str | None = None
    source_url: str | None = None


@dataclass(frozen=True)
class RuntimeReactionArtifact:
    hash_dimension: int
    k_neighbors: int
    direction_neutral_threshold: float
    metadata_fields: list[str]
    training_rows: list[RuntimeReactionRow]
    model_family: str = "knn"
    uses_semantic_text_features: bool = False
    structured_feature_dimension: int = 0
    direction_classes: list[int] | None = None
    direction_intercepts: list[float] | None = None
    direction_coefficients: list[list[float]] | None = None
    magnitude_intercept: float | None = None
    magnitude_coefficients: list[float] | None = None
    direction_calibration_temperature: float | None = None
    horizon_classes: list[int] | None = None
    horizon_intercepts: list[float] | None = None
    horizon_coefficients: list[list[float]] | None = None
    reliability_weights: dict[str, float] | None = None
    recency_half_life_days: float = 180.0
    # Recorded explicitly so a weights-only (linear) artifact, which ships no training
    # rows, can still answer which horizons it was trained on and how often each occurred.
    horizons: list[int] | None = None
    horizon_row_counts: dict[int, int] | None = None
    training_row_count: int | None = None
    # "pair": direction labels are the pair's own return (all artifacts before 2026-10-24).
    # "usd": labels are the dollar's return, so one pooled model can serve USD pairs on
    # both sides of the quote; the scorer flips direction back for pairs quoted in USD.
    direction_orientation: str = "pair"
    # "shared" or "pair_group" — how text tokens were keyed when the model was trained
    # (market.pair_assets.scoped_text_token); the scorer must key them the same way.
    text_token_scope: str = "shared"

    def available_horizons(self) -> list[int]:
        """Returns sorted unique horizons available in the loaded artifact."""
        if self.horizons:
            return sorted(set(self.horizons))
        return sorted({row.horizon_minutes for row in self.training_rows})

    def horizon_counts(self) -> dict[int, int]:
        """Training-row count per horizon, read from the artifact or derived from its rows."""
        if self.horizon_row_counts:
            return dict(self.horizon_row_counts)
        counts: dict[int, int] = {}
        for row in self.training_rows:
            counts[row.horizon_minutes] = counts.get(row.horizon_minutes, 0) + 1
        return counts

    def is_weights_only_linear(self) -> bool:
        """True when inference needs only coefficients, never the training rows."""
        return (
            self.model_family == "linear"
            and self.direction_classes is not None
            and self.direction_intercepts is not None
            and self.direction_coefficients is not None
            and self.magnitude_intercept is not None
            and self.magnitude_coefficients is not None
        )


def load_reaction_artifact(artifact_path: Path) -> RuntimeReactionArtifact:
    """Loads a runtime reaction artifact from a local JSON export."""
    raw_payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    if not isinstance(raw_payload, dict):
        raise ValueError("Reaction artifact payload must decode to a dictionary.")

    raw_training_rows = raw_payload.get("training_rows")
    if raw_training_rows is None:
        raw_training_rows = []
    if not isinstance(raw_training_rows, list):
        raise ValueError("Reaction artifact training_rows must be a list when provided.")
    if not raw_training_rows and not _is_weights_only_payload(raw_payload):
        # k-NN inference needs the rows; only a complete linear artifact that records its
        # horizons may omit them.
        raise ValueError(
            "Reaction artifact must contain a non-empty training_rows list "
            "(only a complete linear artifact with available_horizons may omit it)."
        )

    training_rows: list[RuntimeReactionRow] = []
    for raw_row in raw_training_rows:
        if not isinstance(raw_row, dict):
            raise ValueError("Each reaction artifact training row must be a dictionary.")
        source_metadata = raw_row.get("source_metadata", {})
        feature_vector = raw_row.get("feature_vector", [])
        horizon_feature_vector = raw_row.get("horizon_feature_vector", feature_vector)
        if not isinstance(source_metadata, dict):
            raise ValueError("Reaction artifact source_metadata must be a dictionary.")
        if not isinstance(feature_vector, list) or not all(isinstance(item, int | float) for item in feature_vector):
            raise ValueError("Reaction artifact feature_vector must be a numeric list.")
        if not isinstance(horizon_feature_vector, list) or not all(isinstance(item, int | float) for item in horizon_feature_vector):
            raise ValueError("Reaction artifact horizon_feature_vector must be a numeric list.")
        normalized_metadata = {str(key): str(value) for key, value in source_metadata.items()}
        training_rows.append(
            RuntimeReactionRow(
                event_id=_required_string(raw_row, "event_id"),
                pair=_required_string(raw_row, "pair"),
                horizon_minutes=_required_int(raw_row, "horizon_minutes"),
                timestamp=datetime.fromisoformat(_required_string(raw_row, "timestamp")),
                direction_label=_required_int(raw_row, "direction_label"),
                magnitude_label=_required_float(raw_row, "magnitude_label"),
                feature_vector=[float(item) for item in feature_vector],
                horizon_feature_vector=[float(item) for item in horizon_feature_vector],
                source_metadata=normalized_metadata,
                event_category=_optional_string(raw_row, "event_category") or normalized_metadata.get("event_category"),
                macro_event_type=_optional_string(raw_row, "macro_event_type") or normalized_metadata.get("macro_event_type"),
                scheduled=_optional_bool(raw_row, "scheduled", normalized_metadata.get("scheduled")),
                actual=_optional_float(raw_row, "actual", normalized_metadata.get("actual")),
                forecast=_optional_float(raw_row, "forecast", normalized_metadata.get("forecast")),
                previous=_optional_float(raw_row, "previous", normalized_metadata.get("previous")),
                standardized_surprise=_optional_float(raw_row, "standardized_surprise", normalized_metadata.get("standardized_surprise")),
                calendar_source=_optional_string(raw_row, "calendar_source") or normalized_metadata.get("calendar_source"),
                source_url=_optional_string(raw_row, "source_url") or normalized_metadata.get("source_url"),
            ),
        )

    return RuntimeReactionArtifact(
        hash_dimension=_required_int(raw_payload, "hash_dimension"),
        k_neighbors=_required_int(raw_payload, "k_neighbors"),
        direction_neutral_threshold=_required_float(raw_payload, "direction_neutral_threshold"),
        metadata_fields=_string_list(raw_payload, "metadata_fields"),
        training_rows=training_rows,
        model_family=_optional_string(raw_payload, "model_family") or "knn",
        uses_semantic_text_features=_optional_bool(raw_payload, "uses_semantic_text_features") or False,
        structured_feature_dimension=_optional_int(raw_payload, "structured_feature_dimension") or 0,
        direction_classes=_optional_int_list(raw_payload, "direction_classes"),
        direction_intercepts=_optional_float_list(raw_payload, "direction_intercepts"),
        direction_coefficients=_optional_float_matrix(raw_payload, "direction_coefficients"),
        magnitude_intercept=_optional_float(raw_payload, "magnitude_intercept"),
        magnitude_coefficients=_optional_float_list(raw_payload, "magnitude_coefficients"),
        direction_calibration_temperature=_optional_float(raw_payload, "direction_calibration_temperature"),
        horizon_classes=_optional_int_list(raw_payload, "horizon_classes"),
        horizon_intercepts=_optional_float_list(raw_payload, "horizon_intercepts"),
        horizon_coefficients=_optional_float_matrix(raw_payload, "horizon_coefficients"),
        reliability_weights=_optional_str_float_dict(raw_payload, "reliability_weights"),
        recency_half_life_days=_optional_float(raw_payload, "recency_half_life_days") or 180.0,
        horizons=_optional_int_list(raw_payload, "available_horizons"),
        horizon_row_counts=_optional_int_count_dict(raw_payload, "horizon_row_counts"),
        training_row_count=_optional_int(raw_payload, "training_row_count"),
        direction_orientation=_direction_orientation(raw_payload),
        text_token_scope=_text_token_scope(raw_payload),
    )


def _text_token_scope(payload: dict[str, object]) -> str:
    from .pair_assets import TEXT_TOKEN_SCOPES

    scope = _optional_string(payload, "text_token_scope") or "shared"
    if scope not in TEXT_TOKEN_SCOPES:
        raise ValueError(f"text_token_scope must be one of {sorted(TEXT_TOKEN_SCOPES)}.")
    return scope


DIRECTION_ORIENTATIONS = frozenset({"pair", "usd"})


def _direction_orientation(payload: dict[str, object]) -> str:
    orientation = _optional_string(payload, "direction_orientation") or "pair"
    if orientation not in DIRECTION_ORIENTATIONS:
        raise ValueError(f"direction_orientation must be one of {sorted(DIRECTION_ORIENTATIONS)}.")
    return orientation


def _is_weights_only_payload(payload: dict[str, object]) -> bool:
    """A linear payload is self-sufficient when it has every coefficient and its horizons."""
    required = (
        "direction_classes",
        "direction_intercepts",
        "direction_coefficients",
        "magnitude_intercept",
        "magnitude_coefficients",
        "available_horizons",
    )
    return payload.get("model_family") == "linear" and all(payload.get(key) is not None for key in required)


def _optional_int_count_dict(payload: dict[str, object], key: str) -> dict[int, int] | None:
    """Parse a {horizon: count} map. JSON object keys are strings, so convert them back."""
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a dictionary when provided.")
    counts: dict[int, int] = {}
    for item_key, item_value in value.items():
        if not isinstance(item_value, int) or isinstance(item_value, bool):
            raise ValueError(f"{key} values must be integers when provided.")
        try:
            counts[int(item_key)] = item_value
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} keys must be integer horizons.") from exc
    return counts


def _required_string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string.")
    return value


def _required_int(payload: dict[str, object], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int):
        raise ValueError(f"{key} must be an integer.")
    return value


def _optional_int(payload: dict[str, object], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, int):
        raise ValueError(f"{key} must be an integer when provided.")
    return value


def _required_float(payload: dict[str, object], key: str) -> float:
    value = payload.get(key)
    if not isinstance(value, int | float):
        raise ValueError(f"{key} must be numeric.")
    return float(value)


def _optional_string(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string when provided.")
    return value.strip()


def _optional_float(payload: dict[str, object], key: str, fallback: str | None = None) -> float | None:
    value = payload.get(key)
    if value is None:
        value = fallback
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{key} must be numeric when provided.")
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        stripped_value = value.strip()
        if not stripped_value:
            return None
        try:
            return float(stripped_value)
        except ValueError as exc:
            raise ValueError(f"{key} must be numeric when provided.") from exc
    raise ValueError(f"{key} must be numeric when provided.")


def _optional_bool(payload: dict[str, object], key: str, fallback: str | None = None) -> bool | None:
    value = payload.get(key)
    if value is None:
        value = fallback
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized_value = value.strip().casefold()
        if normalized_value in {"true", "1", "yes"}:
            return True
        if normalized_value in {"false", "0", "no"}:
            return False
    raise ValueError(f"{key} must be boolean when provided.")


def _string_list(payload: dict[str, object], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be a list of strings.")
    return list(value)


def _optional_int_list(payload: dict[str, object], key: str) -> list[int] | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, int) for item in value):
        raise ValueError(f"{key} must be a list of integers when provided.")
    return list(value)


def _optional_float_list(payload: dict[str, object], key: str) -> list[float] | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, int | float) for item in value):
        raise ValueError(f"{key} must be a list of numerics when provided.")
    return [float(item) for item in value]


def _optional_float_matrix(payload: dict[str, object], key: str) -> list[list[float]] | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list of numeric lists when provided.")
    matrix: list[list[float]] = []
    for row in value:
        if not isinstance(row, list) or not all(isinstance(item, int | float) for item in row):
            raise ValueError(f"{key} must be a list of numeric lists when provided.")
        matrix.append([float(item) for item in row])
    return matrix


def _optional_str_float_dict(payload: dict[str, object], key: str) -> dict[str, float] | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a dictionary when provided.")
    converted: dict[str, float] = {}
    for item_key, item_value in value.items():
        if not isinstance(item_value, int | float):
            raise ValueError(f"{key} values must be numeric when provided.")
        converted[str(item_key)] = float(item_value)
    return converted

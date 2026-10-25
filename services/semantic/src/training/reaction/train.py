from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime
import hashlib
import json
import math

from market.models import MarketReactionSignal
from market.pair_assets import scoped_text_token

from ..models import MarketReactionTrainingRow
from .config import ReactionTrainingConfig
from .evaluate import NEGATIVE_DIRECTION_INDEX
from .evaluate import NEUTRAL_DIRECTION_INDEX
from .evaluate import POSITIVE_DIRECTION_INDEX
from .evaluate import ReactionEvaluationResult
from .evaluate import evaluate_horizon_predictions
from .evaluate import evaluate_reaction_predictions

MODEL_FAMILY_KNN = "knn"
MODEL_FAMILY_LINEAR = "linear"
DEFAULT_RECENCY_HALF_LIFE_DAYS = 180.0
CALIBRATION_EPSILON = 1e-9


@dataclass(frozen=True)
class ReactionFeatureRow:
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
    scheduled: bool = False
    actual: float | None = None
    forecast: float | None = None
    previous: float | None = None
    standardized_surprise: float | None = None
    calendar_source: str | None = None
    source_url: str | None = None


@dataclass(frozen=True)
class ReactionModelArtifact:
    hash_dimension: int
    k_neighbors: int
    direction_neutral_threshold: float
    metadata_fields: list[str]
    training_rows: list[ReactionFeatureRow]
    model_family: str = MODEL_FAMILY_KNN
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
    recency_half_life_days: float = DEFAULT_RECENCY_HALF_LIFE_DAYS
    # "pair" = labels are the pair's own return; "usd" = labels are the dollar's return
    # (see market.reaction_artifact.RuntimeReactionArtifact.direction_orientation).
    direction_orientation: str = "pair"
    text_token_scope: str = "shared"


def load_reaction_rows(config: ReactionTrainingConfig) -> list[MarketReactionTrainingRow]:
    """Loads labeled market-reaction rows from a local JSONL file."""
    rows: list[MarketReactionTrainingRow] = []
    for line in config.training_table_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw_row = json.loads(line)
        if not isinstance(raw_row, dict):
            raise ValueError("Each reaction training row must decode to a dictionary.")
        source_metadata = raw_row.get("source_metadata", {})
        if not isinstance(source_metadata, dict):
            raise ValueError("source_metadata must be a dictionary.")
        rows.append(
            MarketReactionTrainingRow(
                article_text=_required_string(raw_row, "article_text"),
                timestamp=datetime.fromisoformat(_required_string(raw_row, "timestamp")),
                pair=_required_string(raw_row, "pair"),
                horizon_minutes=_required_int(raw_row, "horizon_minutes"),
                sentiment_label=_required_float(raw_row, "sentiment_label"),
                reaction_label=_required_float(raw_row, "reaction_label"),
                magnitude_label=_required_float(raw_row, "magnitude_label"),
                source_metadata={str(key): str(value) for key, value in source_metadata.items()},
                event_category=_optional_string(raw_row, "event_category") or source_metadata.get("event_category"),
                macro_event_type=_optional_string(raw_row, "macro_event_type") or source_metadata.get("macro_event_type"),
                scheduled=_optional_bool(raw_row, "scheduled", source_metadata.get("scheduled", "false")),
                actual=_optional_float(raw_row, "actual", source_metadata.get("actual")),
                forecast=_optional_float(raw_row, "forecast", source_metadata.get("forecast")),
                previous=_optional_float(raw_row, "previous", source_metadata.get("previous")),
                standardized_surprise=_optional_float(raw_row, "standardized_surprise", source_metadata.get("standardized_surprise")),
                calendar_source=_optional_string(raw_row, "calendar_source") or source_metadata.get("calendar_source"),
                source_url=_optional_string(raw_row, "source_url") or source_metadata.get("source_url"),
            ),
        )
    if not rows:
        raise ValueError("No reaction training rows were loaded from the configured table.")
    return rows


def assemble_reaction_features(
    row: MarketReactionTrainingRow,
    config: ReactionTrainingConfig,
) -> ReactionFeatureRow:
    """Builds a deterministic feature vector for a reaction training row."""
    feature_vector = _hashed_hybrid_feature_vector(row, config, include_horizon=True)
    horizon_feature_vector = _hashed_hybrid_feature_vector(row, config, include_horizon=False)

    return ReactionFeatureRow(
        event_id=row.source_metadata.get(
            "event_id",
            f"{row.pair}:{row.timestamp.isoformat()}:{row.horizon_minutes}",
        ),
        pair=row.pair,
        horizon_minutes=row.horizon_minutes,
        timestamp=row.timestamp,
        direction_label=_direction_label(row.reaction_label, config.direction_neutral_threshold),
        magnitude_label=row.magnitude_label,
        feature_vector=feature_vector,
        horizon_feature_vector=horizon_feature_vector,
        source_metadata={str(key): str(value) for key, value in row.source_metadata.items()},
        event_category=row.event_category,
        macro_event_type=row.macro_event_type,
        scheduled=row.scheduled,
        actual=row.actual,
        forecast=row.forecast,
        previous=row.previous,
        standardized_surprise=row.standardized_surprise,
        calendar_source=row.calendar_source,
        source_url=row.source_url,
    )


def assemble_semantic_features(
    row: MarketReactionTrainingRow,
    config: ReactionTrainingConfig,
    embedder: object,
) -> ReactionFeatureRow:
    """Builds a dense semantic feature vector using sentence-transformers."""
    feature_vector = _semantic_hybrid_feature_vector(row, embedder, include_horizon=True)
    horizon_feature_vector = _semantic_hybrid_feature_vector(row, embedder, include_horizon=False)

    return ReactionFeatureRow(
        event_id=row.source_metadata.get(
            "event_id",
            f"{row.pair}:{row.timestamp.isoformat()}:{row.horizon_minutes}",
        ),
        pair=row.pair,
        horizon_minutes=row.horizon_minutes,
        timestamp=row.timestamp,
        direction_label=_direction_label(row.reaction_label, config.direction_neutral_threshold),
        magnitude_label=row.magnitude_label,
        feature_vector=feature_vector,
        horizon_feature_vector=horizon_feature_vector,
        source_metadata={str(key): str(value) for key, value in row.source_metadata.items()},
        event_category=row.event_category,
        macro_event_type=row.macro_event_type,
        scheduled=row.scheduled,
        actual=row.actual,
        forecast=row.forecast,
        previous=row.previous,
        standardized_surprise=row.standardized_surprise,
        calendar_source=row.calendar_source,
        source_url=row.source_url,
    )


def time_based_split(
    rows: list[ReactionFeatureRow],
    train_ratio: float,
    validation_ratio: float,
) -> tuple[list[ReactionFeatureRow], list[ReactionFeatureRow], list[ReactionFeatureRow]]:
    """Splits rows into train, validation, and test partitions by timestamp."""
    if not 0 < train_ratio < 1:
        raise ValueError("train_ratio must be between 0 and 1.")
    if not 0 <= validation_ratio < 1:
        raise ValueError("validation_ratio must be between 0 and 1.")
    if train_ratio + validation_ratio >= 1:
        raise ValueError("train_ratio + validation_ratio must be less than 1.")

    ordered_rows = sorted(rows, key=lambda row: row.timestamp)
    total_rows = len(ordered_rows)
    train_cutoff = int(total_rows * train_ratio)
    validation_cutoff = int(total_rows * (train_ratio + validation_ratio))
    return (
        ordered_rows[:train_cutoff],
        ordered_rows[train_cutoff:validation_cutoff],
        ordered_rows[validation_cutoff:],
    )


def release_group_key(row: ReactionFeatureRow) -> str:
    """Rows that come from one release share this key (all pairs, horizons and views).

    Calendar rows carry an explicit `release_id`; other rows fall back to their event id
    stripped of any horizon suffix, then to the timestamp.
    """
    release_id = row.source_metadata.get("release_id")
    if release_id:
        return release_id
    event_id = row.source_metadata.get("event_id")
    if event_id:
        return event_id
    return row.timestamp.isoformat()


def release_grouped_time_split(
    rows: list[ReactionFeatureRow],
    train_ratio: float,
    validation_ratio: float,
) -> tuple[list[ReactionFeatureRow], list[ReactionFeatureRow], list[ReactionFeatureRow]]:
    """Chronological split that never puts rows of one release in two partitions.

    `time_based_split` cuts at a row index, so the four pair-rows (and three horizons) of
    one payrolls release can land on both sides of the cut — the test set then contains a
    release the model has already seen. Here the cut is made on releases, ordered by their
    earliest timestamp, with the ratios applied to release counts.
    """
    if not 0 < train_ratio < 1 or not 0 <= validation_ratio < 1 or train_ratio + validation_ratio >= 1:
        raise ValueError("train_ratio and validation_ratio must satisfy 0 < train, 0 <= val, train + val < 1.")
    groups: dict[str, list[ReactionFeatureRow]] = {}
    for row in rows:
        groups.setdefault(release_group_key(row), []).append(row)
    ordered = sorted(groups.values(), key=lambda group: (min(row.timestamp for row in group), release_group_key(group[0])))
    train_cutoff = int(len(ordered) * train_ratio)
    validation_cutoff = int(len(ordered) * (train_ratio + validation_ratio))
    flatten = lambda chunk: [row for group in chunk for row in group]  # noqa: E731
    return (
        flatten(ordered[:train_cutoff]),
        flatten(ordered[train_cutoff:validation_cutoff]),
        flatten(ordered[validation_cutoff:]),
    )


def train_reaction_model_from_splits(
    config: ReactionTrainingConfig,
    train_rows: list[ReactionFeatureRow],
    validation_rows: list[ReactionFeatureRow],
    *,
    direction_orientation: str = "pair",
) -> ReactionModelArtifact:
    """Fit on caller-chosen partitions; calibration and reliability use `validation_rows`."""
    if not train_rows or not validation_rows:
        raise ValueError("train_rows and validation_rows must both be non-empty.")
    artifact = replace(_train_artifact(config, train_rows), direction_orientation=direction_orientation)
    return _fit_posthoc_components(artifact, validation_rows)


def train_reaction_model(
    config: ReactionTrainingConfig,
) -> tuple[ReactionModelArtifact, ReactionEvaluationResult]:
    """Trains a lightweight market-reaction model with time-based evaluation."""
    rows = load_reaction_rows(config)

    if config.use_semantic_features:
        from retrieval.sentence_transformer_embedder import SentenceTransformerEmbedder
        embedder = SentenceTransformerEmbedder(model_name=config.embedder_model)
        config = config.model_copy(update={"hash_dimension": embedder.dimensions})
        feature_rows = [assemble_semantic_features(row, config, embedder) for row in rows]
    else:
        feature_rows = [assemble_reaction_features(row, config) for row in rows]
    train_rows, validation_rows, test_rows = time_based_split(
        feature_rows,
        config.train_ratio,
        config.validation_ratio,
    )
    if not train_rows or not validation_rows or not test_rows:
        raise ValueError("Time-based split produced an empty train, validation, or test partition.")

    artifact = _train_artifact(config, train_rows)
    artifact = _fit_posthoc_components(artifact, validation_rows)
    evaluation = evaluate_reaction_model(artifact, validation_rows + test_rows)
    return artifact, evaluation


def _train_artifact(
    config: ReactionTrainingConfig,
    train_rows: list[ReactionFeatureRow],
) -> ReactionModelArtifact:
    structured_feature_dimension = max(0, len(train_rows[0].feature_vector) - config.hash_dimension)
    horizon_components = _fit_horizon_components(train_rows)
    common_fields = {
        "hash_dimension": config.hash_dimension,
        "k_neighbors": config.k_neighbors,
        "direction_neutral_threshold": config.direction_neutral_threshold,
        "metadata_fields": list(config.metadata_fields),
        "training_rows": train_rows,
        "uses_semantic_text_features": config.use_semantic_features,
        "structured_feature_dimension": structured_feature_dimension,
        "horizon_classes": None if horizon_components is None else horizon_components["classes"],
        "horizon_intercepts": None if horizon_components is None else horizon_components["intercepts"],
        "horizon_coefficients": None if horizon_components is None else horizon_components["coefficients"],
        "recency_half_life_days": _estimate_recency_half_life_days(train_rows),
        "text_token_scope": config.text_token_scope,
    }
    if config.model_family == MODEL_FAMILY_LINEAR:
        linear_components = _fit_linear_components(
            train_rows,
            class_weight=None if config.direction_class_weight == "none" else "balanced",
        )
        if linear_components is not None:
            return ReactionModelArtifact(
                model_family=MODEL_FAMILY_LINEAR,
                direction_classes=linear_components["direction_classes"],
                direction_intercepts=linear_components["direction_intercepts"],
                direction_coefficients=linear_components["direction_coefficients"],
                magnitude_intercept=linear_components["magnitude_intercept"],
                magnitude_coefficients=linear_components["magnitude_coefficients"],
                **common_fields,
            )

    return ReactionModelArtifact(
        model_family=MODEL_FAMILY_KNN,
        **common_fields,
    )


def _fit_posthoc_components(
    artifact: ReactionModelArtifact,
    validation_rows: list[ReactionFeatureRow],
) -> ReactionModelArtifact:
    calibration_temperature = _fit_direction_calibration_temperature(artifact, validation_rows)
    artifact = replace(artifact, direction_calibration_temperature=calibration_temperature)
    reliability_weights = _learn_reliability_weights(artifact, validation_rows)
    return replace(artifact, reliability_weights=reliability_weights)


def _fit_linear_components(
    train_rows: list[ReactionFeatureRow],
    *,
    class_weight: str | None = "balanced",
) -> dict[str, object] | None:
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.linear_model import Ridge

    direction_labels = [row.direction_label for row in train_rows]
    if len(set(direction_labels)) < 3:
        return None

    feature_matrix = np.array([row.feature_vector for row in train_rows], dtype=np.float64)
    magnitude_targets = np.array([row.magnitude_label for row in train_rows], dtype=np.float64)
    classifier = LogisticRegression(
        class_weight=class_weight,
        max_iter=2000,
        solver="lbfgs",
        C=0.75,
    )
    classifier.fit(feature_matrix, np.array(direction_labels, dtype=np.int32))

    magnitude_regressor = Ridge(alpha=1.0)
    magnitude_regressor.fit(feature_matrix, magnitude_targets)

    return {
        "direction_classes": [int(value) for value in classifier.classes_.tolist()],
        "direction_intercepts": [float(value) for value in classifier.intercept_.tolist()],
        "direction_coefficients": [
            [float(weight) for weight in row_weights]
            for row_weights in classifier.coef_.tolist()
        ],
        "magnitude_intercept": float(magnitude_regressor.intercept_),
        "magnitude_coefficients": [float(value) for value in magnitude_regressor.coef_.tolist()],
    }


def _fit_horizon_components(train_rows: list[ReactionFeatureRow]) -> dict[str, object] | None:
    import numpy as np
    from sklearn.linear_model import LogisticRegression

    horizon_targets = [row.horizon_minutes for row in train_rows]
    if len(set(horizon_targets)) < 2:
        return None

    feature_matrix = np.array([row.horizon_feature_vector for row in train_rows], dtype=np.float64)
    classifier = LogisticRegression(
        class_weight="balanced",
        max_iter=2000,
        solver="lbfgs",
        C=1.0,
    )
    classifier.fit(feature_matrix, np.array(horizon_targets, dtype=np.int32))
    return {
        "classes": [int(value) for value in classifier.classes_.tolist()],
        "intercepts": [float(value) for value in classifier.intercept_.tolist()],
        "coefficients": [
            [float(weight) for weight in row_weights]
            for row_weights in classifier.coef_.tolist()
        ],
    }


def evaluate_reaction_model(
    artifact: ReactionModelArtifact,
    rows: list[ReactionFeatureRow],
) -> ReactionEvaluationResult:
    """Evaluates a trained reaction artifact against held-out rows (numpy-accelerated)."""
    if _has_linear_model(artifact):
        predicted_probabilities = [_direction_probabilities(artifact, row) for row in rows]
        predicted_magnitudes = [_linear_predicted_magnitude(artifact, row) for row in rows]
        evaluation_result = evaluate_reaction_predictions(
            true_directions=[row.direction_label for row in rows],
            predicted_probabilities=predicted_probabilities,
            true_magnitudes=[row.magnitude_label for row in rows],
            predicted_magnitudes=predicted_magnitudes,
            pairs=[row.pair for row in rows],
            horizons=[row.horizon_minutes for row in rows],
            macro_event_types=[row.macro_event_type for row in rows],
            scheduled_flags=[row.scheduled for row in rows],
        )
        if _has_horizon_model(artifact):
            return replace(
                evaluation_result,
                horizon_prediction=evaluate_horizon_predictions(
                    true_horizons=[row.horizon_minutes for row in rows],
                    predicted_horizons=[_predicted_horizon(artifact, row) for row in rows],
                ),
            )
        return evaluation_result

    import numpy as np

    k = artifact.k_neighbors
    train_rows = artifact.training_rows

    # Pre-compute normalised training matrix once
    train_mat = np.array([r.feature_vector for r in train_rows], dtype=np.float32)
    train_norms = np.linalg.norm(train_mat, axis=1, keepdims=True)
    train_mat_n = train_mat / np.maximum(train_norms, 1e-10)

    test_mat = np.array([r.feature_vector for r in rows], dtype=np.float32)
    test_norms = np.linalg.norm(test_mat, axis=1, keepdims=True)
    test_mat_n = test_mat / np.maximum(test_norms, 1e-10)

    # Batch cosine similarity: (n_test, n_train) via single matmul
    sim_matrix = test_mat_n @ train_mat_n.T

    # Pre-extract training labels/magnitudes as arrays
    train_dirs = np.array([r.direction_label for r in train_rows], dtype=np.int32)
    train_mags = np.array([r.magnitude_label for r in train_rows], dtype=np.float64)

    predicted_probabilities: list[list[float]] = []
    predicted_magnitudes: list[float] = []

    for i in range(len(rows)):
        sims = sim_matrix[i]
        top_k_idx = np.argpartition(sims, -k)[-k:]
        top_k_sims = np.maximum(sims[top_k_idx], 0.0).astype(np.float64)

        # Direction probabilities
        weights = [0.0, 0.0, 0.0]
        for j, idx in enumerate(top_k_idx):
            dir_idx = _direction_to_index(int(train_dirs[idx]))
            weights[dir_idx] += float(top_k_sims[j])
        tw = sum(weights)
        probs = [w / tw for w in weights] if tw > 0 else [0.0, 1.0, 0.0]
        probs = _apply_direction_calibration(probs, artifact.direction_calibration_temperature)
        predicted_probabilities.append(probs)

        # Magnitude
        total_w = float(top_k_sims.sum())
        if total_w > 0:
            predicted_magnitudes.append(float(np.dot(top_k_sims, train_mags[top_k_idx]) / total_w))
        else:
            predicted_magnitudes.append(float(train_mags[top_k_idx].mean()) if len(top_k_idx) > 0 else 0.0)

    evaluation_result = evaluate_reaction_predictions(
        true_directions=[row.direction_label for row in rows],
        predicted_probabilities=predicted_probabilities,
        true_magnitudes=[row.magnitude_label for row in rows],
        predicted_magnitudes=predicted_magnitudes,
        pairs=[row.pair for row in rows],
        horizons=[row.horizon_minutes for row in rows],
        macro_event_types=[row.macro_event_type for row in rows],
        scheduled_flags=[row.scheduled for row in rows],
    )
    if _has_horizon_model(artifact):
        return replace(
            evaluation_result,
            horizon_prediction=evaluate_horizon_predictions(
                true_horizons=[row.horizon_minutes for row in rows],
                predicted_horizons=[_predicted_horizon(artifact, row) for row in rows],
            ),
        )
    return evaluation_result


def predict_reaction(
    artifact: ReactionModelArtifact,
    row: ReactionFeatureRow,
) -> MarketReactionSignal:
    """Predicts a market reaction for one feature row."""
    probabilities = _direction_probabilities(artifact, row)
    predicted_direction = _probability_to_direction(probabilities)
    predicted_magnitude = _predicted_magnitude(artifact, row)
    confidence = max(probabilities)
    return MarketReactionSignal(
        event_id=row.event_id,
        symbol=row.pair,
        direction=float(predicted_direction),
        magnitude=predicted_magnitude,
        confidence=confidence,
        horizon_minutes=row.horizon_minutes,
        key_drivers=_key_drivers(row),
        produced_at=row.timestamp,
    )


def _direction_label(reaction_label: float, neutral_threshold: float) -> int:
    if reaction_label > neutral_threshold:
        return 1
    if reaction_label < -neutral_threshold:
        return -1
    return 0


def _direction_probabilities(
    artifact: ReactionModelArtifact,
    row: ReactionFeatureRow,
) -> list[float]:
    probabilities = _uncalibrated_direction_probabilities(artifact, row)
    return _apply_direction_calibration(probabilities, artifact.direction_calibration_temperature)


def _uncalibrated_direction_probabilities(
    artifact: ReactionModelArtifact,
    row: ReactionFeatureRow,
) -> list[float]:
    if _has_linear_model(artifact):
        return _linear_direction_probabilities(artifact, row)

    neighbors = _nearest_neighbors(artifact.training_rows, row, artifact.k_neighbors)
    if not neighbors:
        return [0.0, 1.0, 0.0]

    weights = [0.0, 0.0, 0.0]
    for neighbor, similarity in neighbors:
        direction_index = _direction_to_index(neighbor.direction_label)
        weights[direction_index] += max(similarity, 0.0)

    total_weight = sum(weights)
    if total_weight == 0:
        return [0.0, 1.0, 0.0]
    return [weight / total_weight for weight in weights]


def _predicted_magnitude(
    artifact: ReactionModelArtifact,
    row: ReactionFeatureRow,
) -> float:
    if _has_linear_model(artifact):
        return _linear_predicted_magnitude(artifact, row)

    neighbors = _nearest_neighbors(artifact.training_rows, row, artifact.k_neighbors)
    if not neighbors:
        return 0.0
    total_weight = sum(max(similarity, 0.0) for _, similarity in neighbors)
    if total_weight == 0:
        return sum(neighbor.magnitude_label for neighbor, _ in neighbors) / len(neighbors)
    weighted_sum = sum(neighbor.magnitude_label * max(similarity, 0.0) for neighbor, similarity in neighbors)
    return weighted_sum / total_weight


def _nearest_neighbors(
    training_rows: list[ReactionFeatureRow],
    row: ReactionFeatureRow,
    k_neighbors: int,
) -> list[tuple[ReactionFeatureRow, float]]:
    ranked_rows = sorted(
        ((training_row, _cosine_similarity(training_row.feature_vector, row.feature_vector)) for training_row in training_rows),
        key=lambda item: item[1],
        reverse=True,
    )
    return ranked_rows[:k_neighbors]


def _has_linear_model(artifact: ReactionModelArtifact) -> bool:
    return (
        artifact.model_family == MODEL_FAMILY_LINEAR
        and artifact.direction_classes is not None
        and artifact.direction_intercepts is not None
        and artifact.direction_coefficients is not None
        and artifact.magnitude_intercept is not None
        and artifact.magnitude_coefficients is not None
    )


def _has_horizon_model(artifact: ReactionModelArtifact) -> bool:
    return (
        artifact.horizon_classes is not None
        and artifact.horizon_intercepts is not None
        and artifact.horizon_coefficients is not None
    )


def _linear_direction_probabilities(
    artifact: ReactionModelArtifact,
    row: ReactionFeatureRow,
) -> list[float]:
    if not _has_linear_model(artifact):
        return [0.0, 1.0, 0.0]

    logits: list[float] = []
    for intercept, weights in zip(artifact.direction_intercepts or [], artifact.direction_coefficients or []):
        logits.append(float(intercept + sum(weight * value for weight, value in zip(weights, row.feature_vector))))
    if not logits:
        return [0.0, 1.0, 0.0]

    exp_logits = _softmax(logits)
    probabilities = [0.0, 0.0, 0.0]
    for direction_class, class_probability in zip(artifact.direction_classes or [], exp_logits):
        probabilities[_direction_to_index(direction_class)] = class_probability
    return probabilities


def _linear_predicted_magnitude(
    artifact: ReactionModelArtifact,
    row: ReactionFeatureRow,
) -> float:
    if artifact.magnitude_intercept is None or artifact.magnitude_coefficients is None:
        return 0.0
    return float(
        artifact.magnitude_intercept
        + sum(weight * value for weight, value in zip(artifact.magnitude_coefficients, row.feature_vector))
    )


def _predicted_horizon(
    artifact: ReactionModelArtifact,
    row: ReactionFeatureRow,
) -> int:
    if not _has_horizon_model(artifact):
        return row.horizon_minutes
    probabilities = _horizon_probabilities(artifact, row)
    best_index = max(range(len(probabilities)), key=lambda index: probabilities[index])
    return int((artifact.horizon_classes or [row.horizon_minutes])[best_index])


def _horizon_probabilities(
    artifact: ReactionModelArtifact,
    row: ReactionFeatureRow,
) -> list[float]:
    if not _has_horizon_model(artifact):
        return [1.0]

    logits: list[float] = []
    for intercept, weights in zip(artifact.horizon_intercepts or [], artifact.horizon_coefficients or []):
        logits.append(float(intercept + sum(weight * value for weight, value in zip(weights, row.horizon_feature_vector))))
    return _softmax(logits)


def _apply_direction_calibration(
    probabilities: list[float],
    temperature: float | None,
) -> list[float]:
    if temperature is None or abs(temperature - 1.0) < 1e-9:
        return probabilities
    logits = [math.log(max(probability, CALIBRATION_EPSILON)) for probability in probabilities]
    return _softmax([logit / temperature for logit in logits])


def _fit_direction_calibration_temperature(
    artifact: ReactionModelArtifact,
    validation_rows: list[ReactionFeatureRow],
) -> float:
    if not validation_rows:
        return 1.0

    true_directions = [row.direction_label for row in validation_rows]
    raw_probabilities = [_uncalibrated_direction_probabilities(artifact, row) for row in validation_rows]

    best_temperature = 1.0
    best_score = float("inf")
    for step in range(10, 61):
        candidate_temperature = step / 20.0
        calibrated_probabilities = [
            _apply_direction_calibration(probabilities, candidate_temperature)
            for probabilities in raw_probabilities
        ]
        candidate_score = _multiclass_brier_score(true_directions, calibrated_probabilities)
        if candidate_score < best_score:
            best_score = candidate_score
            best_temperature = candidate_temperature
    return best_temperature


def _learn_reliability_weights(
    artifact: ReactionModelArtifact,
    validation_rows: list[ReactionFeatureRow],
) -> dict[str, float]:
    if not validation_rows:
        return {}

    grouped_scores: dict[str, list[float]] = {}
    for row in validation_rows:
        predicted_direction = _probability_to_direction(_direction_probabilities(artifact, row))
        predicted_magnitude = _predicted_magnitude(artifact, row)
        direction_score = 1.0 if predicted_direction == row.direction_label else 0.0
        magnitude_scale = max(abs(row.magnitude_label), artifact.direction_neutral_threshold, 1e-4)
        magnitude_score = max(0.0, 1.0 - (abs(predicted_magnitude - row.magnitude_label) / (magnitude_scale * 3.0)))
        learned_score = 0.6 * direction_score + 0.4 * magnitude_score
        for reliability_key in _reliability_keys(row):
            grouped_scores.setdefault(reliability_key, []).append(learned_score)

    learned_weights: dict[str, float] = {}
    for reliability_key, scores in grouped_scores.items():
        average_score = sum(scores) / len(scores)
        shrinkage = min(1.0, len(scores) / 5.0)
        shrunken_score = 0.5 + (average_score - 0.5) * shrinkage
        learned_weights[reliability_key] = _clip(0.7 + shrunken_score * 0.7, 0.6, 1.4)
    return learned_weights


def _reliability_keys(row: ReactionFeatureRow) -> list[str]:
    source = row.source_metadata.get("source", "unknown").strip().casefold() or "unknown"
    event_family = _normalized_optional_string(row.macro_event_type) or _normalized_optional_string(row.event_category) or "unknown"
    pair = row.pair.strip().upper()
    return [
        f"source={source}|event_family={event_family}|pair={pair}",
        f"source={source}|event_family={event_family}",
        f"source={source}|pair={pair}",
        f"event_family={event_family}|pair={pair}",
        f"source={source}",
        f"event_family={event_family}",
        f"pair={pair}",
    ]


def _estimate_recency_half_life_days(train_rows: list[ReactionFeatureRow]) -> float:
    if len(train_rows) < 2:
        return DEFAULT_RECENCY_HALF_LIFE_DAYS
    ordered_timestamps = sorted(row.timestamp for row in train_rows)
    span_days = max(1.0, (ordered_timestamps[-1] - ordered_timestamps[0]).total_seconds() / 86400.0)
    return _clip(span_days / 3.0, 30.0, 365.0)


def _hashed_hybrid_feature_vector(
    row: MarketReactionTrainingRow,
    config: ReactionTrainingConfig,
    *,
    include_horizon: bool,
) -> list[float]:
    values = [0.0 for _ in range(config.hash_dimension)]
    for token in _text_tokens(row.article_text):
        _update_feature(values, scoped_text_token(token, row.pair, config.text_token_scope), config.hash_dimension, 1.0)
    _update_feature(values, f"pair={row.pair}", config.hash_dimension, 1.0)
    if include_horizon:
        _update_feature(values, f"horizon={row.horizon_minutes}", config.hash_dimension, 1.0)

    event_family = row.macro_event_type or row.event_category
    if event_family is not None and event_family.strip():
        _update_feature(values, f"event_family={event_family.strip().casefold()}", config.hash_dimension, 1.0)
    _update_feature(values, f"scheduled={'true' if row.scheduled else 'false'}", config.hash_dimension, 1.0)

    resolved_surprise = _resolved_surprise_value(row)
    if resolved_surprise is not None:
        _update_feature(values, f"surprise_bucket={_surprise_bucket(resolved_surprise)}", config.hash_dimension, 1.0)

    for metadata_field in config.metadata_fields:
        metadata_value = _metadata_value(row, metadata_field)
        if metadata_value is None or not metadata_value.strip():
            continue
        _update_feature(
            values,
            f"{metadata_field}={metadata_value.strip().casefold()}",
            config.hash_dimension,
            1.0,
        )

    return _normalize_vector(values + _structured_feature_values(row, include_horizon=include_horizon))


def _semantic_hybrid_feature_vector(
    row: MarketReactionTrainingRow,
    embedder: object,
    *,
    include_horizon: bool,
) -> list[float]:
    semantic_text = _semantic_text(row, include_horizon=include_horizon)
    text_vector = [float(value) for value in embedder.embed(semantic_text)]
    return _normalize_vector(text_vector + _structured_feature_values(row, include_horizon=include_horizon))


def _semantic_text(
    row: MarketReactionTrainingRow,
    *,
    include_horizon: bool,
) -> str:
    parts = [row.article_text, f"pair {row.pair}"]
    if include_horizon:
        parts.append(f"horizon {row.horizon_minutes} minutes")
    event_family = row.macro_event_type or row.event_category
    if event_family is not None and event_family.strip():
        parts.append(f"event family {event_family.strip()}")
    parts.append("scheduled" if row.scheduled else "unscheduled")
    resolved_surprise = _resolved_surprise_value(row)
    if resolved_surprise is not None:
        parts.append(f"standardized surprise {resolved_surprise:.4f}")
    if row.actual is not None:
        parts.append(f"actual {row.actual}")
    if row.forecast is not None:
        parts.append(f"forecast {row.forecast}")
    if row.previous is not None:
        parts.append(f"previous {row.previous}")
    return " ; ".join(parts)


def _structured_feature_values(
    row: MarketReactionTrainingRow,
    *,
    include_horizon: bool,
) -> list[float]:
    resolved_surprise = _resolved_surprise_value(row)
    normalized_surprise = 0.0 if resolved_surprise is None else _clip(resolved_surprise, -3.0, 3.0) / 3.0
    surprise_gap = 0.0
    if row.actual is not None and row.forecast is not None:
        scale = abs(row.forecast)
        if scale < 1e-12 and row.previous is not None:
            scale = abs(row.previous)
        if scale < 1e-12:
            scale = 1.0
        surprise_gap = _clip((row.actual - row.forecast) / scale, -3.0, 3.0) / 3.0

    return [
        min(float(row.horizon_minutes), 1440.0) / 1440.0 if include_horizon else 0.0,
        1.0 if row.scheduled else 0.0,
        normalized_surprise,
        abs(normalized_surprise),
        surprise_gap,
        1.0 if row.actual is not None else 0.0,
        1.0 if row.forecast is not None else 0.0,
        1.0 if row.previous is not None else 0.0,
    ]


def _resolved_surprise_value(row: MarketReactionTrainingRow) -> float | None:
    if row.standardized_surprise is not None:
        return row.standardized_surprise
    if row.actual is None or row.forecast is None:
        return None
    scale = abs(row.forecast)
    if scale < 1e-12 and row.previous is not None:
        scale = abs(row.previous)
    if scale < 1e-12:
        scale = 1.0
    return (row.actual - row.forecast) / scale


def _surprise_bucket(value: float) -> str:
    if value <= -1.0:
        return "large-negative"
    if value < -0.2:
        return "negative"
    if value < 0.2:
        return "neutral"
    if value < 1.0:
        return "positive"
    return "large-positive"


def _normalized_optional_string(value: str | None) -> str | None:
    if value is None:
        return None
    normalized_value = value.strip().casefold()
    if not normalized_value:
        return None
    return normalized_value


def _softmax(logits: list[float]) -> list[float]:
    if not logits:
        return []
    max_logit = max(logits)
    exp_logits = [math.exp(logit - max_logit) for logit in logits]
    denominator = sum(exp_logits)
    if denominator <= 0:
        return [0.0 for _ in logits]
    return [value / denominator for value in exp_logits]


def _multiclass_brier_score(true_directions: list[int], predicted_probabilities: list[list[float]]) -> float:
    total_score = 0.0
    for true_direction, probability_row in zip(true_directions, predicted_probabilities):
        row_score = 0.0
        true_index = _direction_to_index(true_direction)
        for label_index, predicted_probability in enumerate(probability_row):
            expected_probability = 1.0 if label_index == true_index else 0.0
            row_score += (predicted_probability - expected_probability) ** 2
        total_score += row_score
    return total_score / len(true_directions)


def _clip(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _direction_to_index(direction: int) -> int:
    if direction == -1:
        return NEGATIVE_DIRECTION_INDEX
    if direction == 0:
        return NEUTRAL_DIRECTION_INDEX
    if direction == 1:
        return POSITIVE_DIRECTION_INDEX
    raise ValueError(f"Unsupported direction label: {direction}")


def _probability_to_direction(probabilities: list[float]) -> int:
    max_index = max(range(len(probabilities)), key=lambda index: probabilities[index])
    if max_index == NEGATIVE_DIRECTION_INDEX:
        return -1
    if max_index == NEUTRAL_DIRECTION_INDEX:
        return 0
    return 1


def _key_drivers(row: ReactionFeatureRow) -> list[str]:
    key_drivers = [row.pair, f"horizon:{row.horizon_minutes}m"]
    if row.macro_event_type is not None and row.macro_event_type.strip():
        key_drivers.append(f"event:{row.macro_event_type}")
    if row.scheduled:
        key_drivers.append("scheduled")
    source_value = row.source_metadata.get("source")
    if source_value is not None:
        key_drivers.append(f"source:{source_value}")
    return key_drivers


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    numerator = sum(left_value * right_value for left_value, right_value in zip(left, right))
    left_norm = sum(left_value * left_value for left_value in left) ** 0.5
    right_norm = sum(right_value * right_value for right_value in right) ** 0.5
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _normalize_vector(values: list[float]) -> list[float]:
    norm = sum(value * value for value in values) ** 0.5
    if norm == 0:
        return values
    return [value / norm for value in values]


def _update_feature(values: list[float], token: str, hash_dimension: int, weight: float) -> None:
    feature_index = _stable_hash(token, hash_dimension)
    values[feature_index] += weight


def _stable_hash(token: str, hash_dimension: int) -> int:
    token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return int(token_digest[:16], 16) % hash_dimension


def _text_tokens(text: str) -> list[str]:
    normalized_text = "".join(character.lower() if character.isalnum() else " " for character in text)
    return [token for token in normalized_text.split() if token]


def _required_string(raw_row: dict[str, object], key: str) -> str:
    value = raw_row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string.")
    return value


def _required_int(raw_row: dict[str, object], key: str) -> int:
    value = raw_row.get(key)
    if not isinstance(value, int):
        raise ValueError(f"{key} must be an integer.")
    return value


def _required_float(raw_row: dict[str, object], key: str) -> float:
    value = raw_row.get(key)
    if not isinstance(value, int | float):
        raise ValueError(f"{key} must be numeric.")
    return float(value)


def _optional_string(raw_row: dict[str, object], key: str) -> str | None:
    value = raw_row.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string when provided.")
    stripped_value = value.strip()
    if not stripped_value:
        return None
    return stripped_value


def _optional_float(raw_row: dict[str, object], key: str, fallback: str | None = None) -> float | None:
    value = raw_row.get(key)
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


def _optional_bool(raw_row: dict[str, object], key: str, fallback: str | None = None) -> bool:
    value = raw_row.get(key)
    if value is None:
        value = fallback
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized_value = value.strip().casefold()
        if normalized_value in {"true", "1", "yes"}:
            return True
        if normalized_value in {"false", "0", "no"}:
            return False
    raise ValueError(f"{key} must be boolean when provided.")


def _metadata_value(row: MarketReactionTrainingRow, metadata_field: str) -> str | None:
    explicit_metadata = {
        "event_category": row.event_category,
        "macro_event_type": row.macro_event_type,
        "scheduled": "true" if row.scheduled else "false",
        "calendar_source": row.calendar_source,
        "source_url": row.source_url,
        "actual": None if row.actual is None else str(row.actual),
        "forecast": None if row.forecast is None else str(row.forecast),
        "previous": None if row.previous is None else str(row.previous),
        "standardized_surprise": None if row.standardized_surprise is None else str(row.standardized_surprise),
    }
    metadata_value = explicit_metadata.get(metadata_field)
    if metadata_value is not None:
        return metadata_value
    return row.source_metadata.get(metadata_field)

from __future__ import annotations

from dataclasses import dataclass

NEGATIVE_DIRECTION_INDEX = 0
NEUTRAL_DIRECTION_INDEX = 1
POSITIVE_DIRECTION_INDEX = 2
NUM_DIRECTION_CLASSES = 3


@dataclass(frozen=True)
class ReactionEvaluationResult:
    magnitude_mae: float
    direction_accuracy: float
    direction_macro_f1: float
    confusion_matrix: list[list[int]]
    calibration_score: float
    pair_breakdown: dict[str, dict[str, float]]
    horizon_breakdown: dict[str, dict[str, float]]
    event_family_breakdown: dict[str, dict[str, float]]
    scheduled_breakdown: dict[str, dict[str, float]]
    horizon_prediction: dict[str, object] | None = None


def evaluate_reaction_predictions(
    true_directions: list[int],
    predicted_probabilities: list[list[float]],
    true_magnitudes: list[float],
    predicted_magnitudes: list[float],
    pairs: list[str],
    horizons: list[int] | None = None,
    macro_event_types: list[str | None] | None = None,
    scheduled_flags: list[bool] | None = None,
) -> ReactionEvaluationResult:
    """Calculates evaluation metrics for market-reaction predictions."""
    item_count = len(true_directions)
    if not item_count:
        raise ValueError("At least one reaction prediction is required for evaluation.")
    if not (
        item_count
        == len(predicted_probabilities)
        == len(true_magnitudes)
        == len(predicted_magnitudes)
        == len(pairs)
    ):
        raise ValueError("Reaction evaluation inputs must all have the same length.")
    _validate_optional_length(horizons, item_count, "horizons")
    _validate_optional_length(macro_event_types, item_count, "macro_event_types")
    _validate_optional_length(scheduled_flags, item_count, "scheduled_flags")

    predicted_directions = [_index_to_direction(_argmax(probability_row)) for probability_row in predicted_probabilities]
    confusion_matrix = _confusion_matrix(true_directions, predicted_directions)
    direction_accuracy = _accuracy(true_directions, predicted_directions)
    direction_macro_f1 = _macro_f1(confusion_matrix)
    magnitude_mae = _mean_absolute_error(true_magnitudes, predicted_magnitudes)
    calibration_score = _multiclass_brier_score(true_directions, predicted_probabilities)
    pair_breakdown = _group_breakdown(
        true_directions,
        predicted_directions,
        true_magnitudes,
        predicted_magnitudes,
        pairs,
    )
    horizon_breakdown = _group_breakdown(
        true_directions,
        predicted_directions,
        true_magnitudes,
        predicted_magnitudes,
        [str(horizon) for horizon in horizons] if horizons is not None else None,
    )
    event_family_breakdown = _group_breakdown(
        true_directions,
        predicted_directions,
        true_magnitudes,
        predicted_magnitudes,
        [_normalize_group_key(event_type) for event_type in macro_event_types] if macro_event_types is not None else None,
    )
    scheduled_breakdown = _group_breakdown(
        true_directions,
        predicted_directions,
        true_magnitudes,
        predicted_magnitudes,
        [_scheduled_group_key(flag) for flag in scheduled_flags] if scheduled_flags is not None else None,
    )
    return ReactionEvaluationResult(
        magnitude_mae=magnitude_mae,
        direction_accuracy=direction_accuracy,
        direction_macro_f1=direction_macro_f1,
        confusion_matrix=confusion_matrix,
        calibration_score=calibration_score,
        pair_breakdown=pair_breakdown,
        horizon_breakdown=horizon_breakdown,
        event_family_breakdown=event_family_breakdown,
        scheduled_breakdown=scheduled_breakdown,
    )


def evaluate_horizon_predictions(
    true_horizons: list[int],
    predicted_horizons: list[int],
) -> dict[str, object]:
    if not true_horizons:
        raise ValueError("At least one horizon prediction is required for evaluation.")
    if len(true_horizons) != len(predicted_horizons):
        raise ValueError("Horizon evaluation inputs must have the same length.")

    accuracy = sum(
        1 for true_horizon, predicted_horizon in zip(true_horizons, predicted_horizons)
        if true_horizon == predicted_horizon
    ) / len(true_horizons)

    breakdown: dict[str, dict[str, float]] = {}
    for horizon in sorted(set(true_horizons)):
        matching_indexes = [index for index, value in enumerate(true_horizons) if value == horizon]
        if not matching_indexes:
            continue
        correct = sum(1 for index in matching_indexes if predicted_horizons[index] == horizon)
        breakdown[str(horizon)] = {
            "count": float(len(matching_indexes)),
            "accuracy": correct / len(matching_indexes),
        }

    return {
        "accuracy": accuracy,
        "breakdown": breakdown,
    }


def _direction_to_index(direction: int) -> int:
    if direction == -1:
        return NEGATIVE_DIRECTION_INDEX
    if direction == 0:
        return NEUTRAL_DIRECTION_INDEX
    if direction == 1:
        return POSITIVE_DIRECTION_INDEX
    raise ValueError(f"Unsupported direction label: {direction}")


def _index_to_direction(index: int) -> int:
    if index == NEGATIVE_DIRECTION_INDEX:
        return -1
    if index == NEUTRAL_DIRECTION_INDEX:
        return 0
    if index == POSITIVE_DIRECTION_INDEX:
        return 1
    raise ValueError(f"Unsupported direction index: {index}")


def _argmax(values: list[float]) -> int:
    best_index = 0
    best_value = values[0]
    for index, value in enumerate(values[1:], start=1):
        if value > best_value:
            best_index = index
            best_value = value
    return best_index


def _accuracy(true_values: list[int], predicted_values: list[int]) -> float:
    correct = sum(1 for true_value, predicted_value in zip(true_values, predicted_values) if true_value == predicted_value)
    return correct / len(true_values)


def _mean_absolute_error(true_values: list[float], predicted_values: list[float]) -> float:
    total_error = sum(abs(true_value - predicted_value) for true_value, predicted_value in zip(true_values, predicted_values))
    return total_error / len(true_values)


def _confusion_matrix(true_directions: list[int], predicted_directions: list[int]) -> list[list[int]]:
    matrix = [[0 for _ in range(NUM_DIRECTION_CLASSES)] for _ in range(NUM_DIRECTION_CLASSES)]
    for true_direction, predicted_direction in zip(true_directions, predicted_directions):
        matrix[_direction_to_index(true_direction)][_direction_to_index(predicted_direction)] += 1
    return matrix


def _macro_f1(confusion_matrix: list[list[int]]) -> float:
    f1_scores: list[float] = []
    for label_index in range(NUM_DIRECTION_CLASSES):
        true_positive = confusion_matrix[label_index][label_index]
        false_positive = sum(confusion_matrix[row_index][label_index] for row_index in range(NUM_DIRECTION_CLASSES) if row_index != label_index)
        false_negative = sum(confusion_matrix[label_index][column_index] for column_index in range(NUM_DIRECTION_CLASSES) if column_index != label_index)

        precision_denominator = true_positive + false_positive
        recall_denominator = true_positive + false_negative
        precision = true_positive / precision_denominator if precision_denominator else 0.0
        recall = true_positive / recall_denominator if recall_denominator else 0.0

        if precision + recall == 0:
            f1_scores.append(0.0)
            continue
        f1_scores.append((2 * precision * recall) / (precision + recall))

    return sum(f1_scores) / len(f1_scores)


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


def _validate_optional_length(values: list[object] | None, expected_length: int, field_name: str) -> None:
    if values is None:
        return
    if len(values) != expected_length:
        raise ValueError(f"Reaction evaluation field '{field_name}' must match prediction length.")


def _group_breakdown(
    true_directions: list[int],
    predicted_directions: list[int],
    true_magnitudes: list[float],
    predicted_magnitudes: list[float],
    group_keys: list[str] | None,
) -> dict[str, dict[str, float]]:
    if group_keys is None:
        return {}

    grouped_rows: dict[str, list[tuple[int, int, float, float]]] = {}
    for group_key, true_direction, predicted_direction, true_magnitude, predicted_magnitude in zip(
        group_keys,
        true_directions,
        predicted_directions,
        true_magnitudes,
        predicted_magnitudes,
    ):
        grouped_rows.setdefault(group_key, []).append(
            (true_direction, predicted_direction, true_magnitude, predicted_magnitude),
        )

    breakdown: dict[str, dict[str, float]] = {}
    for group_key, rows in grouped_rows.items():
        grouped_true_directions = [row[0] for row in rows]
        grouped_predicted_directions = [row[1] for row in rows]
        grouped_true_magnitudes = [row[2] for row in rows]
        grouped_predicted_magnitudes = [row[3] for row in rows]
        breakdown[group_key] = {
            "count": float(len(rows)),
            "direction_accuracy": _accuracy(grouped_true_directions, grouped_predicted_directions),
            "magnitude_mae": _mean_absolute_error(grouped_true_magnitudes, grouped_predicted_magnitudes),
        }
    return breakdown


def _normalize_group_key(value: str | None) -> str:
    if value is None:
        return "unknown"
    normalized_value = value.strip()
    if not normalized_value:
        return "unknown"
    return normalized_value


def _scheduled_group_key(value: bool) -> str:
    return "scheduled" if value else "unscheduled"

from __future__ import annotations

from dataclasses import dataclass

NEGATIVE_LABEL = 0
NEUTRAL_LABEL = 1
POSITIVE_LABEL = 2
NUM_LABELS = 3


@dataclass(frozen=True)
class SentimentEvaluationResult:
    accuracy: float
    macro_f1: float
    confusion_matrix: list[list[int]]
    calibration_score: float


def evaluate_sentiment_predictions(
    true_labels: list[int],
    predicted_probabilities: list[list[float]],
) -> SentimentEvaluationResult:
    """Calculates sentiment classification metrics from probabilities."""
    if len(true_labels) != len(predicted_probabilities):
        raise ValueError("true_labels and predicted_probabilities must have the same length.")
    if not true_labels:
        raise ValueError("At least one prediction is required for evaluation.")

    predicted_labels: list[int] = []
    for probability_row in predicted_probabilities:
        if len(probability_row) != NUM_LABELS:
            raise ValueError("Each probability row must contain three class probabilities.")
        predicted_labels.append(_argmax(probability_row))

    confusion_matrix = _confusion_matrix(true_labels, predicted_labels)
    accuracy = _accuracy(true_labels, predicted_labels)
    macro_f1 = _macro_f1(confusion_matrix)
    calibration_score = _multiclass_brier_score(true_labels, predicted_probabilities)
    return SentimentEvaluationResult(
        accuracy=accuracy,
        macro_f1=macro_f1,
        confusion_matrix=confusion_matrix,
        calibration_score=calibration_score,
    )


def _argmax(values: list[float]) -> int:
    best_index = 0
    best_value = values[0]
    for index, value in enumerate(values[1:], start=1):
        if value > best_value:
            best_index = index
            best_value = value
    return best_index


def _accuracy(true_labels: list[int], predicted_labels: list[int]) -> float:
    correct = sum(1 for true_label, predicted_label in zip(true_labels, predicted_labels) if true_label == predicted_label)
    return correct / len(true_labels)


def _confusion_matrix(true_labels: list[int], predicted_labels: list[int]) -> list[list[int]]:
    matrix = [[0 for _ in range(NUM_LABELS)] for _ in range(NUM_LABELS)]
    for true_label, predicted_label in zip(true_labels, predicted_labels):
        matrix[true_label][predicted_label] += 1
    return matrix


def _macro_f1(confusion_matrix: list[list[int]]) -> float:
    f1_scores: list[float] = []
    for label_index in range(NUM_LABELS):
        true_positive = confusion_matrix[label_index][label_index]
        false_positive = sum(confusion_matrix[row_index][label_index] for row_index in range(NUM_LABELS) if row_index != label_index)
        false_negative = sum(confusion_matrix[label_index][column_index] for column_index in range(NUM_LABELS) if column_index != label_index)

        precision_denominator = true_positive + false_positive
        recall_denominator = true_positive + false_negative
        precision = true_positive / precision_denominator if precision_denominator else 0.0
        recall = true_positive / recall_denominator if recall_denominator else 0.0

        if precision + recall == 0:
            f1_scores.append(0.0)
            continue
        f1_scores.append((2 * precision * recall) / (precision + recall))

    return sum(f1_scores) / len(f1_scores)


def _multiclass_brier_score(true_labels: list[int], predicted_probabilities: list[list[float]]) -> float:
    total_score = 0.0
    for true_label, probability_row in zip(true_labels, predicted_probabilities):
        row_score = 0.0
        for label_index, predicted_probability in enumerate(probability_row):
            expected_probability = 1.0 if true_label == label_index else 0.0
            row_score += (predicted_probability - expected_probability) ** 2
        total_score += row_score
    return total_score / len(true_labels)

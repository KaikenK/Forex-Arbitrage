from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from training.reaction.evaluate import ReactionEvaluationResult
from training.reaction.evaluate import evaluate_reaction_predictions
from training.sentiment.evaluate import SentimentEvaluationResult
from training.sentiment.evaluate import evaluate_sentiment_predictions


@dataclass(frozen=True)
class BacktestPredictionRow:
    timestamp: datetime
    pair: str
    sentiment_true_label: int
    sentiment_predicted_probabilities: list[float]
    reaction_true_direction: int
    reaction_predicted_probabilities: list[float]
    reaction_true_magnitude: float
    reaction_predicted_magnitude: float
    horizon_minutes: int = 60
    event_category: str | None = None
    macro_event_type: str | None = None
    scheduled: bool = False
    actual: float | None = None
    forecast: float | None = None
    previous: float | None = None
    standardized_surprise: float | None = None


@dataclass(frozen=True)
class BacktestFoldMetrics:
    fold_index: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    sentiment: SentimentEvaluationResult
    reaction: ReactionEvaluationResult


@dataclass(frozen=True)
class BacktestSummary:
    folds: list[BacktestFoldMetrics]
    sentiment_accuracy: float
    sentiment_macro_f1: float
    sentiment_calibration: float
    reaction_direction_accuracy: float
    reaction_direction_macro_f1: float
    reaction_calibration: float
    reaction_magnitude_mae: float
    pair_breakdown: dict[str, dict[str, float]]
    horizon_breakdown: dict[str, dict[str, float]]
    event_family_breakdown: dict[str, dict[str, float]]
    scheduled_breakdown: dict[str, dict[str, float]]


def evaluate_backtest_fold(
    fold_index: int,
    train_rows: list[BacktestPredictionRow],
    test_rows: list[BacktestPredictionRow],
) -> BacktestFoldMetrics:
    """Evaluates one walk-forward fold for sentiment and market reaction."""
    if not train_rows:
        raise ValueError("train_rows must not be empty for a backtest fold.")
    if not test_rows:
        raise ValueError("test_rows must not be empty for a backtest fold.")

    sentiment_metrics = evaluate_sentiment_predictions(
        true_labels=[row.sentiment_true_label for row in test_rows],
        predicted_probabilities=[row.sentiment_predicted_probabilities for row in test_rows],
    )
    reaction_metrics = evaluate_reaction_predictions(
        true_directions=[row.reaction_true_direction for row in test_rows],
        predicted_probabilities=[row.reaction_predicted_probabilities for row in test_rows],
        true_magnitudes=[row.reaction_true_magnitude for row in test_rows],
        predicted_magnitudes=[row.reaction_predicted_magnitude for row in test_rows],
        pairs=[row.pair for row in test_rows],
        horizons=[row.horizon_minutes for row in test_rows],
        macro_event_types=[row.macro_event_type for row in test_rows],
        scheduled_flags=[row.scheduled for row in test_rows],
    )
    ordered_train_rows = sorted(train_rows, key=lambda row: row.timestamp)
    ordered_test_rows = sorted(test_rows, key=lambda row: row.timestamp)
    return BacktestFoldMetrics(
        fold_index=fold_index,
        train_start=ordered_train_rows[0].timestamp,
        train_end=ordered_train_rows[-1].timestamp,
        test_start=ordered_test_rows[0].timestamp,
        test_end=ordered_test_rows[-1].timestamp,
        sentiment=sentiment_metrics,
        reaction=reaction_metrics,
    )


def summarize_backtest(folds: list[BacktestFoldMetrics]) -> BacktestSummary:
    """Aggregates walk-forward fold metrics into one summary."""
    if not folds:
        raise ValueError("At least one fold is required to summarize a backtest.")

    sentiment_accuracy = sum(fold.sentiment.accuracy for fold in folds) / len(folds)
    sentiment_macro_f1 = sum(fold.sentiment.macro_f1 for fold in folds) / len(folds)
    sentiment_calibration = sum(fold.sentiment.calibration_score for fold in folds) / len(folds)
    reaction_direction_accuracy = sum(fold.reaction.direction_accuracy for fold in folds) / len(folds)
    reaction_direction_macro_f1 = sum(fold.reaction.direction_macro_f1 for fold in folds) / len(folds)
    reaction_calibration = sum(fold.reaction.calibration_score for fold in folds) / len(folds)
    reaction_magnitude_mae = sum(fold.reaction.magnitude_mae for fold in folds) / len(folds)
    pair_breakdown = _aggregate_breakdown(folds, lambda reaction: reaction.pair_breakdown)
    horizon_breakdown = _aggregate_breakdown(folds, lambda reaction: reaction.horizon_breakdown)
    event_family_breakdown = _aggregate_breakdown(folds, lambda reaction: reaction.event_family_breakdown)
    scheduled_breakdown = _aggregate_breakdown(folds, lambda reaction: reaction.scheduled_breakdown)

    return BacktestSummary(
        folds=folds,
        sentiment_accuracy=sentiment_accuracy,
        sentiment_macro_f1=sentiment_macro_f1,
        sentiment_calibration=sentiment_calibration,
        reaction_direction_accuracy=reaction_direction_accuracy,
        reaction_direction_macro_f1=reaction_direction_macro_f1,
        reaction_calibration=reaction_calibration,
        reaction_magnitude_mae=reaction_magnitude_mae,
        pair_breakdown=pair_breakdown,
        horizon_breakdown=horizon_breakdown,
        event_family_breakdown=event_family_breakdown,
        scheduled_breakdown=scheduled_breakdown,
    )


def _aggregate_breakdown(
    folds: list[BacktestFoldMetrics],
    selector: Callable[[ReactionEvaluationResult], dict[str, dict[str, float]]],
) -> dict[str, dict[str, float]]:
    grouped_values: dict[str, list[dict[str, float]]] = {}
    for fold in folds:
        for group_key, metrics in selector(fold.reaction).items():
            grouped_values.setdefault(group_key, []).append(metrics)

    aggregated: dict[str, dict[str, float]] = {}
    for group_key, metrics_list in grouped_values.items():
        aggregated[group_key] = {
            "count": sum(metric["count"] for metric in metrics_list),
            "direction_accuracy": sum(metric["direction_accuracy"] for metric in metrics_list) / len(metrics_list),
            "magnitude_mae": sum(metric["magnitude_mae"] for metric in metrics_list) / len(metrics_list),
        }
    return aggregated

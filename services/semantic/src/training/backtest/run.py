from __future__ import annotations

from training.backtest.metrics import BacktestFoldMetrics
from training.backtest.metrics import BacktestPredictionRow
from training.backtest.metrics import BacktestSummary
from training.backtest.metrics import evaluate_backtest_fold
from training.backtest.metrics import summarize_backtest


def walk_forward_splits(
    rows: list[BacktestPredictionRow],
    train_window_size: int,
    test_window_size: int,
) -> list[tuple[list[BacktestPredictionRow], list[BacktestPredictionRow]]]:
    """Builds sequential walk-forward train and test folds from ordered rows."""
    if train_window_size <= 0:
        raise ValueError("train_window_size must be greater than zero.")
    if test_window_size <= 0:
        raise ValueError("test_window_size must be greater than zero.")

    ordered_rows = sorted(rows, key=lambda row: row.timestamp)
    splits: list[tuple[list[BacktestPredictionRow], list[BacktestPredictionRow]]] = []
    start_index = 0
    while start_index + train_window_size + test_window_size <= len(ordered_rows):
        train_rows = ordered_rows[start_index : start_index + train_window_size]
        test_rows = ordered_rows[start_index + train_window_size : start_index + train_window_size + test_window_size]
        splits.append((train_rows, test_rows))
        start_index += test_window_size
    return splits


def run_walk_forward_backtest(
    rows: list[BacktestPredictionRow],
    train_window_size: int,
    test_window_size: int,
) -> BacktestSummary:
    """Runs walk-forward evaluation across all sequential folds."""
    splits = walk_forward_splits(rows, train_window_size=train_window_size, test_window_size=test_window_size)
    if not splits:
        raise ValueError("Walk-forward backtest requires enough rows for at least one fold.")

    fold_metrics: list[BacktestFoldMetrics] = []
    for fold_index, (train_rows, test_rows) in enumerate(splits):
        fold_metrics.append(evaluate_backtest_fold(fold_index, train_rows, test_rows))
    return summarize_backtest(fold_metrics)

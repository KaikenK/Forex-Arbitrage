from __future__ import annotations

import json
import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path

import pytest

PROJECT_SRC = Path(__file__).resolve().parents[2] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.append(str(PROJECT_SRC))

from training.backtest.report import build_report_payload
from training.backtest.report import write_backtest_report
from training.backtest.run import run_walk_forward_backtest
from training.backtest.run import walk_forward_splits
from training.backtest.metrics import BacktestPredictionRow


def _rows() -> list[BacktestPredictionRow]:
    base_time = datetime(2026, 3, 20, 9, 0, tzinfo=UTC)
    return [
        BacktestPredictionRow(
            timestamp=base_time.replace(day=20 + index),
            pair="EUR/USD" if index % 2 == 0 else "USD/JPY",
            sentiment_true_label=2 if index % 2 == 0 else 0,
            sentiment_predicted_probabilities=[0.1, 0.1, 0.8] if index % 2 == 0 else [0.7, 0.2, 0.1],
            reaction_true_direction=1 if index % 2 == 0 else -1,
            reaction_predicted_probabilities=[0.1, 0.1, 0.8] if index % 2 == 0 else [0.7, 0.2, 0.1],
            reaction_true_magnitude=0.02 if index % 2 == 0 else 0.03,
            reaction_predicted_magnitude=0.021 if index % 2 == 0 else 0.028,
            horizon_minutes=15 if index % 2 == 0 else 60,
            macro_event_type="cpi" if index % 2 == 0 else "rate-decision",
            scheduled=index % 2 == 0,
        )
        for index in range(6)
    ]


def test_walk_forward_splits_keep_time_order() -> None:
    splits = walk_forward_splits(_rows(), train_window_size=3, test_window_size=1)

    assert len(splits) == 3
    assert splits[0][0][0].timestamp < splits[0][1][0].timestamp
    assert splits[1][0][-1].timestamp < splits[1][1][0].timestamp


def test_run_walk_forward_backtest_returns_pair_and_event_level_metrics() -> None:
    summary = run_walk_forward_backtest(_rows(), train_window_size=3, test_window_size=1)

    assert len(summary.folds) == 3
    assert summary.sentiment_accuracy == pytest.approx(1.0)
    assert summary.reaction_direction_accuracy == pytest.approx(1.0)
    assert summary.reaction_magnitude_mae == pytest.approx((0.002 + 0.001 + 0.002) / 3)
    assert "EUR/USD" in summary.pair_breakdown or "USD/JPY" in summary.pair_breakdown
    assert "15" in summary.horizon_breakdown or "60" in summary.horizon_breakdown
    assert "cpi" in summary.event_family_breakdown or "rate-decision" in summary.event_family_breakdown
    assert "scheduled" in summary.scheduled_breakdown or "unscheduled" in summary.scheduled_breakdown


def test_backtest_report_is_json_serializable(tmp_path: Path) -> None:
    summary = run_walk_forward_backtest(_rows(), train_window_size=3, test_window_size=1)

    payload = build_report_payload(summary)
    report_path = write_backtest_report(summary, tmp_path)

    assert payload["sentiment_accuracy"] == pytest.approx(1.0)
    assert report_path.exists()
    persisted_payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert persisted_payload["reaction_direction_accuracy"] == pytest.approx(1.0)
    assert "horizon_breakdown" in persisted_payload
    assert "scheduled_breakdown" in persisted_payload

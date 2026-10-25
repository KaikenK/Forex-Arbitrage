from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_SRC = Path(__file__).resolve().parents[2] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.append(str(PROJECT_SRC))

from training.reaction.evaluate import evaluate_reaction_predictions


def test_evaluate_reaction_predictions_reports_metrics_and_event_breakdowns() -> None:
    evaluation = evaluate_reaction_predictions(
        true_directions=[1, -1, 0, 1],
        predicted_probabilities=[
            [0.1, 0.2, 0.7],
            [0.7, 0.2, 0.1],
            [0.2, 0.6, 0.2],
            [0.2, 0.5, 0.3],
        ],
        true_magnitudes=[0.02, 0.01, 0.00, 0.03],
        predicted_magnitudes=[0.018, 0.012, 0.002, 0.025],
        pairs=["EUR/USD", "EUR/USD", "GBP/USD", "GBP/USD"],
        horizons=[15, 15, 60, 60],
        macro_event_types=["cpi", "cpi", "rate-decision", None],
        scheduled_flags=[True, True, False, False],
    )

    assert evaluation.magnitude_mae == pytest.approx(0.00275)
    assert evaluation.direction_accuracy == pytest.approx(0.75)
    assert evaluation.direction_macro_f1 == pytest.approx(0.7777777778)
    assert evaluation.confusion_matrix == [
        [1, 0, 0],
        [0, 1, 0],
        [0, 1, 1],
    ]
    assert evaluation.calibration_score == pytest.approx(0.325)
    assert evaluation.pair_breakdown["EUR/USD"]["direction_accuracy"] == pytest.approx(1.0)
    assert evaluation.pair_breakdown["GBP/USD"]["direction_accuracy"] == pytest.approx(0.5)
    assert evaluation.horizon_breakdown["15"]["direction_accuracy"] == pytest.approx(1.0)
    assert evaluation.horizon_breakdown["60"]["direction_accuracy"] == pytest.approx(0.5)
    assert evaluation.event_family_breakdown["cpi"]["count"] == pytest.approx(2.0)
    assert evaluation.event_family_breakdown["unknown"]["count"] == pytest.approx(1.0)
    assert evaluation.scheduled_breakdown["scheduled"]["direction_accuracy"] == pytest.approx(1.0)
    assert evaluation.scheduled_breakdown["unscheduled"]["direction_accuracy"] == pytest.approx(0.5)

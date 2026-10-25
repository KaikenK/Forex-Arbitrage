from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_SRC = Path(__file__).resolve().parents[2] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.append(str(PROJECT_SRC))

from training.sentiment.evaluate import evaluate_sentiment_predictions


def test_evaluate_sentiment_predictions_calculates_metrics() -> None:
    evaluation = evaluate_sentiment_predictions(
        true_labels=[0, 1, 2, 2],
        predicted_probabilities=[
            [0.8, 0.1, 0.1],
            [0.2, 0.7, 0.1],
            [0.1, 0.2, 0.7],
            [0.6, 0.2, 0.2],
        ],
    )

    assert evaluation.accuracy == pytest.approx(0.75)
    assert evaluation.macro_f1 == pytest.approx(0.7777777778)
    assert evaluation.confusion_matrix == [
        [1, 0, 0],
        [0, 1, 0],
        [1, 0, 1],
    ]
    assert evaluation.calibration_score == pytest.approx(0.345)

"""Dashboard model reports for the weights-only pooled model (end-to-end test, 2026-10-25).

The pooled artifact ships no rows, so /evaluation came back as {"error": ...} and the
dashboard crashed on `.toFixed` of a missing metric; the benchmark panel still read a file
from the old sibling news_stream folder. Both now come from the shipped training_report.json.
"""
from __future__ import annotations

import json

from backend.core.sentiment_bridge import _summarize_pooled_report

REPORT = {
    "generated_at": "2026-10-25T00:00:00+00:00",
    "test": {
        "usdinr": {
            "USD/INR|archive": {"rows": 80, "direction_accuracy": 0.55, "direction_macro_f1": 0.26,
                                "majority_baseline_accuracy": 0.76, "surprise_word_rule_accuracy": 0.76,
                                "magnitude_mae_bps": 12.3},
            "USD/INR|archive|1440m": {"rows": 41, "direction_accuracy": 0.12},
        },
        "calendar": {
            "EUR/USD|live": {"rows": 549, "direction_accuracy": 0.428, "direction_macro_f1": 0.40,
                             "majority_baseline_accuracy": 0.375, "magnitude_mae_bps": 17.4},
            "EUR/USD|calendar": {"rows": 549, "direction_accuracy": 0.415},
        },
    },
    "benchmarks": {"calendar": {"EUR/USD|live": {"knn_accuracy": 0.404, "svm_tfidf_accuracy": 0.393}}},
}


def test_pooled_report_lists_only_served_views_with_honest_nulls():
    summary = _summarize_pooled_report(REPORT)
    names = [d["dataset_name"] for d in summary["datasets"]]
    assert names == ["EUR/USD|live", "USD/INR|archive"], "calendar view and horizon slices are not served live"
    eur = summary["datasets"][0]
    families = {m["model_family"]: m for m in eur["models"]}
    assert families["pooled linear (shipped)"]["direction_accuracy"] == 0.428
    assert families["pooled linear (shipped)"]["magnitude_mae"] == round(17.4e-4, 6)
    assert families["k-NN"]["direction_macro_f1"] is None, "unknown metrics are None, not 0"
    assert "surprise-word rule" not in families, "absent metric -> no row"
    assert eur["best_model"] == "pooled linear (shipped)"


def test_service_evaluation_comes_from_the_training_report(tmp_path):
    import importlib.util
    import sys
    from pathlib import Path

    serve_path = Path(__file__).resolve().parents[1] / "services" / "semantic" / "serve.py"
    sys.path.insert(0, str(serve_path.parent / "src"))
    try:
        spec = importlib.util.spec_from_file_location("semantic_serve_under_test", serve_path)
        serve = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(serve)
    finally:
        sys.path.pop(0)
    artifact = tmp_path / "reaction_model_artifact.json"
    artifact.write_text(json.dumps({"model_family": "linear", "weights_only": True}), encoding="utf-8")
    (tmp_path / "training_report.json").write_text(json.dumps(REPORT), encoding="utf-8")
    evaluation = serve._load_evaluation_from_artifact(artifact)
    assert evaluation["holdout_rows"] == 549 + 80
    expected = (549 * 0.428 + 80 * 0.55) / (549 + 80)
    assert abs(evaluation["direction_accuracy"] - expected) < 1e-9
    assert evaluation["calibration_score"] is None
    assert set(evaluation["per_view"]) == {"EUR/USD|live", "USD/INR|archive"}
    artifact.write_text(json.dumps({"evaluation": {"direction_accuracy": 0.6}}), encoding="utf-8")
    assert serve._load_evaluation_from_artifact(artifact) == {"direction_accuracy": 0.6}, "embedded block wins"
